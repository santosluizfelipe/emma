-- Derived store: everything the agent reads lives here.
-- The source database is only ever read, during ingestion.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Reference data (small; fully refreshed on each run)
CREATE TABLE IF NOT EXISTS service_users (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    date_of_birth TEXT,
    sex           TEXT,
    status        TEXT,
    org_unit_id   INTEGER
);

CREATE TABLE IF NOT EXISTS carers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT,
    org_unit_id INTEGER
);

-- Visits without the JSON blob; activities are flattened below
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY,
    service_user_id INTEGER NOT NULL,
    carer_id        INTEGER,
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    status          TEXT,
    note            TEXT,
    content_hash    TEXT              -- hash of note + activities, drives re-extraction
);
CREATE INDEX IF NOT EXISTS idx_visits_su_time ON visits (service_user_id, start_time);
CREATE INDEX IF NOT EXISTS idx_visits_time    ON visits (start_time);
CREATE INDEX IF NOT EXISTS idx_visits_carer   ON visits (carer_id, start_time);

-- One row per task inside a visit (from visits.activities JSON)
CREATE TABLE IF NOT EXISTS visit_activities (
    visit_id      INTEGER NOT NULL,
    position      INTEGER NOT NULL,
    name          TEXT,
    status        TEXT,
    description   TEXT,
    comment       TEXT,
    is_medication INTEGER NOT NULL DEFAULT 0,
    closed_at     TEXT,
    PRIMARY KEY (visit_id, position)
);
CREATE INDEX IF NOT EXISTS idx_activities_name ON visit_activities (name, status);

-- Form header with its type resolved
CREATE TABLE IF NOT EXISTS forms (
    id              INTEGER PRIMARY KEY,
    form_config_id  INTEGER,
    form_name       TEXT,   -- e.g. "Care Plan", "Moving and Handling"
    form_type       TEXT,   -- e.g. "Care Plans", "Risk Assessments"
    entity_type     TEXT,   -- "service_user" | "organisation"
    instance_title  TEXT,
    org_unit_id     INTEGER,
    created_at      TEXT
);

-- One row per question/answer (from forms.form_data JSON)
CREATE TABLE IF NOT EXISTS form_answers (
    form_id  INTEGER NOT NULL,
    position INTEGER NOT NULL,
    section  TEXT,
    question TEXT,
    answer   TEXT,
    PRIMARY KEY (form_id, position)
);
CREATE INDEX IF NOT EXISTS idx_form_answers_question ON form_answers (question);

-- Who each form is about. Safeguarding forms have no service_user_id in the
-- source; the person is only named inside the JSON, so we resolve it here.
CREATE TABLE IF NOT EXISTS form_subjects (
    form_id         INTEGER PRIMARY KEY,
    service_user_id INTEGER,          -- NULL when unresolved
    confidence      REAL NOT NULL,    -- 1.0 direct/exact, <1.0 fuzzy, 0 unmatched
    method          TEXT NOT NULL,    -- direct | exact | fuzzy | ambiguous | unmatched | not_applicable
    matched_text    TEXT              -- the name as written in the form
);
CREATE INDEX IF NOT EXISTS idx_form_subjects_su ON form_subjects (service_user_id);

-- Anything a human should check instead of the system guessing
CREATE TABLE IF NOT EXISTS review_queue (
    record_type TEXT NOT NULL,
    record_id   INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (record_type, record_id, reason)
);

-- =========================================================== Step 2: signals
-- What the extractor found in each visit. Most routine visits have none.
CREATE TABLE IF NOT EXISTS visit_signals (
    id                     INTEGER PRIMARY KEY,
    visit_id               INTEGER NOT NULL,
    service_user_id        INTEGER NOT NULL,
    observed_at            TEXT NOT NULL,
    day                    TEXT NOT NULL,
    category               TEXT NOT NULL,
    severity               TEXT NOT NULL,      -- low | medium | high
    severity_score         INTEGER NOT NULL,   -- 1 | 2 | 3
    evidence               TEXT NOT NULL,      -- verbatim quote from the record
    source                 TEXT NOT NULL,      -- note | activity_comment
    escalated              INTEGER NOT NULL,   -- "reported to office", "GP called"...
    matches_care_plan_risk INTEGER NOT NULL,   -- the care plan lists this as a known risk
    contradicts_status     INTEGER NOT NULL,   -- e.g. medication "Completed" but not given
    evidence_verified      INTEGER NOT NULL,   -- quote found in the source text
    extractor              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_su_day  ON visit_signals (service_user_id, day);
CREATE INDEX IF NOT EXISTS idx_signals_visit   ON visit_signals (visit_id);
CREATE INDEX IF NOT EXISTS idx_signals_day_sev ON visit_signals (day, severity_score);

-- One row per visit processed: makes extraction incremental and idempotent
CREATE TABLE IF NOT EXISTS extraction_runs (
    visit_id     INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL,
    extractor    TEXT NOT NULL,
    signal_count INTEGER NOT NULL,
    error        TEXT,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================== Step 3: rules + roll-ups
CREATE TABLE IF NOT EXISTS record_issues (
    record_type     TEXT NOT NULL,     -- visit | service_user | signal
    record_id       INTEGER NOT NULL,
    service_user_id INTEGER,
    day             TEXT,
    issue           TEXT NOT NULL,     -- completed_without_comment | status_contradiction | ...
    detail          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (record_type, record_id, issue, detail)
);
CREATE INDEX IF NOT EXISTS idx_issues_su_day ON record_issues (service_user_id, day);
CREATE INDEX IF NOT EXISTS idx_issues_day    ON record_issues (day, issue);

CREATE TABLE IF NOT EXISTS su_daily_risk (
    service_user_id INTEGER NOT NULL,
    day             TEXT NOT NULL,
    score           INTEGER NOT NULL,  -- severity-weighted signals that day
    rolling_7d      INTEGER NOT NULL,  -- sum over the last 7 days
    categories      TEXT NOT NULL,     -- JSON list of categories seen in the 7 days
    top_evidence    TEXT NOT NULL,     -- JSON list of visit refs, most severe first
    alert           TEXT NOT NULL,     -- none | trend | immediate
    PRIMARY KEY (service_user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_risk_day ON su_daily_risk (day, rolling_7d);

-- =========================================================== Step 4: search
-- One chunk per visit, one per form section. ref is what answers cite (V205, F11).
CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY,
    key             TEXT UNIQUE NOT NULL,  -- V205 | F11#2
    ref             TEXT NOT NULL,         -- V205 | F11
    record_type     TEXT NOT NULL,         -- visit | form
    record_id       INTEGER NOT NULL,
    service_user_id INTEGER,
    day             TEXT,
    title           TEXT,
    text            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedded_hash   TEXT                   -- content_hash when last embedded
);
CREATE INDEX IF NOT EXISTS idx_chunks_su_day ON chunks (service_user_id, day);

-- Keyword search (BM25). Porter stemming: "bruising" matches "bruise".
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize = 'porter unicode61');

-- Embeddings (source of truth for vectors; an ANN index can be rebuilt from it)
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id        INTEGER PRIMARY KEY,
    service_user_id INTEGER,
    day             TEXT,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    vector          BLOB NOT NULL          -- float32, L2-normalised
);
CREATE INDEX IF NOT EXISTS idx_embeddings_su_day ON chunk_embeddings (service_user_id, day);
