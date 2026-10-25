"""Step 3a: deterministic checks that need no LLM.

Incremental: only visits and people queued by normalise/extract since the last run are
checked (see the queue tables in schema.sql), so the work per run follows what changed,
not how much history or how many people there are. The first run of a new rules
version checks everything once.

Issues written to record_issues:
  * completed_without_comment  - task marked Completed with no comment (one row per task)
  * status_contradiction       - the record says medication wasn't given, but the
                                 Medication task is marked Completed
  * unverified_evidence        - an extracted quote couldn't be found in the visit
  * assessment_not_reviewed    - a high-severity signal happened after the person's
                                 latest care plan / risk assessment
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import List

from app import db

RULES_VERSION = "2"  # bump when a rule changes: the next run re-checks everything

VISIT_ISSUES = ("completed_without_comment", "status_contradiction", "unverified_evidence")


def window_start(as_of: str, days: int) -> str:
    return (date.fromisoformat(db.day_of(as_of)) - timedelta(days=days)).isoformat()


def check_visits(idx: sqlite3.Connection, ids: List[int]) -> None:
    """Visit-level rules for these visits (replacing what was there)."""
    m = db.marks(ids)
    idx.execute(
        f"DELETE FROM record_issues WHERE record_type = 'visit' AND record_id IN ({m}) "
        f"AND issue IN ({db.marks(VISIT_ISSUES)})", [*ids, *VISIT_ISSUES])
    idx.execute(
        f"""
        INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
        SELECT 'visit', v.id, v.service_user_id, substr(v.start_time, 1, 10), 'completed_without_comment', a.name
        FROM visit_activities a
        JOIN visits v ON v.id = a.visit_id
        WHERE v.id IN ({m}) AND a.status = 'Completed' AND (a.comment IS NULL OR trim(a.comment) = '')
        """, ids)
    idx.execute(
        f"""
        INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
        SELECT 'visit', s.visit_id, s.service_user_id, s.day, 'status_contradiction',
               'Medication task marked Completed; record says: ' || s.evidence
        FROM visit_signals s
        WHERE s.visit_id IN ({m}) AND s.contradicts_status = 1
        """, ids)
    idx.execute(
        f"""
        INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
        SELECT 'visit', s.visit_id, s.service_user_id, s.day, 'unverified_evidence',
               'Quote not found in the visit: ' || s.evidence
        FROM visit_signals s
        WHERE s.visit_id IN ({m}) AND s.evidence_verified = 0
        """, ids)


def check_people(idx: sqlite3.Connection, ids: List[int]) -> None:
    """Person-level rules: latest high-severity signal vs latest care plan / risk assessment."""
    m = db.marks(ids)
    idx.execute(
        f"DELETE FROM record_issues WHERE record_type = 'service_user' AND record_id IN ({m}) "
        "AND issue = 'assessment_not_reviewed'", ids)
    idx.execute(
        f"""
        INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
        WITH latest_high AS (
            SELECT service_user_id, MAX(day) AS day
            FROM visit_signals
            WHERE service_user_id IN ({m}) AND severity = 'high'
            GROUP BY service_user_id
        ),
        latest_form AS (
            -- SQLite returns f.id from the row holding MAX(created_at)
            SELECT fs.service_user_id, f.form_name, MAX(f.created_at) AS created_at, f.id AS form_id
            FROM forms f JOIN form_subjects fs ON fs.form_id = f.id
            WHERE fs.service_user_id IN ({m}) AND f.form_name IN ('Care Plan', 'Moving and Handling')
            GROUP BY fs.service_user_id, f.form_name
        )
        SELECT 'service_user', h.service_user_id, h.service_user_id, h.day,
               'assessment_not_reviewed',
               lf.form_name || ' F' || lf.form_id || ' last updated ' || lf.created_at
               || '; high-severity signal on ' || h.day
        FROM latest_high h
        JOIN latest_form lf ON lf.service_user_id = h.service_user_id
        WHERE lf.created_at < h.day
        """, ids + ids)


def run(idx: sqlite3.Connection) -> dict:
    if db.get_meta(idx, "rules:version") != RULES_VERSION:
        with idx:  # first run of these rules: check everything once
            idx.execute("DELETE FROM record_issues")
            idx.execute("INSERT OR IGNORE INTO queue_rules_visits SELECT id FROM visits")
            idx.execute("INSERT OR IGNORE INTO queue_rules_people SELECT DISTINCT service_user_id FROM visit_signals")
            db.set_meta(idx, "rules:version", RULES_VERSION)

    visits = db.drain(idx, "queue_rules_visits", lambda ids: check_visits(idx, ids))
    people = db.drain(idx, "queue_rules_people", lambda ids: check_people(idx, ids))
    return {"visits_checked": visits, "people_checked": people}
