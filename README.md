# Care Data Agent

An agent that lets care managers ask questions about home-care visits and clinical
forms, and surfaces how service users are doing and where the risks are.

> Status: complete. Back end (steps 1-5), front end (step 6) and evaluation (step 7).

## The key observation

The structured fields say everything is fine: every visit and every task is marked
*Completed*, every visit lasts exactly 45 minutes. The risks only appear in the free
text, sometimes as a single sentence in an otherwise routine note, and two of the most
serious ones (a gradual decline over a week) only show up when notes are read in order.

So the design reads every note **once**, when it arrives, and turns it into structured,
cited signals. Questions are then answered from small, indexed, pre-computed tables.

## Architecture

```
WRITE PATH (once per record)                          READ PATH (per question)

care_data.db (read-only)                               care manager
   │                                                       │  question
   ▼                                                       ▼
1 normalise   flatten JSON, link forms to people       agent loop (OpenAI tool calling)
2 extract     note → signals (category, severity,          │  picks tools, max 8 rounds
              verbatim quote, escalated)                   ▼
3 rules       gaps, contradictions, overdue reviews    9 bounded, read-only tools
  rollup      daily + 7-day risk score, alerts             │
4 index       chunks → FTS5 (BM25) + embeddings            ▼
   │                                                   answer with [V205]-style citations,
   ▼                                                   checked against what tools returned
agent_index.db  ◄──────────────────────────────────────────┘
```

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate     # Python 3.10+ recommended (3.9 works)
pip install -r requirements.txt
cp .env.example .env    # set SOURCE_DB_PATH and OPENAI_API_KEY
```

## Run

```bash
python -m app.ingest                       # build everything (LLM extraction + embeddings)
python -m app.ingest --extractor keyword   # offline, no API key needed
python -m app.agent                        # chat in the terminal
python -m app.agent "Who should I worry about this week?"
uvicorn app.api:app --reload --port 8000   # HTTP API for the front end
pytest -q                                  # 61 tests on synthetic data (see Testing and evaluation)
```

Front end (second terminal, Node 18+):

```bash
cd web
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
```

Each step can also run alone: `python -m app.ingest.normalise`, `python -m app.ingest.extract
--max-visits 20` (a cheap trial of the LLM extractor), `python -m app.search.index`.

## Layout

```
backend/app/
  config.py, db.py, schema.sql, llm.py
  ingest/
    normalise/        step 1  copy + flatten, incremental (watermarks), batched
      common.py               batching, JSON parsing, review flags
      reference.py            service users, carers
      visits.py               visits + visit_activities
      forms.py                forms, form_answers, who each form is about
    linking.py                resolve "Service user involved" names to IDs, safely
    signals.py        step 2  signal taxonomy (Pydantic)
    extractors.py             LLMExtractor (structured output) + KeywordExtractor baseline
    extract.py                runner: incremental, concurrent, verifies quotes
    rules.py          step 3  documentation gaps, contradictions, overdue reviews
    rollup.py                 daily / 7-day scores and alerts
  search/
    index.py          step 4  chunking, FTS5, embeddings (incremental by content hash)
    embeddings.py             OpenAI embeddings (+ hash embedder for tests)
    vectors.py                sqlite-vec, with a pure-Python fallback
    hybrid.py                 keyword + vector, Reciprocal Rank Fusion
  agent/
    tools/            step 5  the 9 tools; each module keeps a tool next to its schema
      context.py              ToolContext + shared helpers
      people.py               find_service_user
      risk.py                 risk_overview, signal_timeline, record_issues
      records.py              get_visit_notes, get_care_plan, safeguarding_cases
      search.py               search_records
      sql.py                  query_sql
      __init__.py             registry: merges every module's SPECS
    sql_guard.py              read-only SQL guardrails
    loop.py                   tool-use loop + citation check
  synthetic/                  generated datasets with ground truth (tests, evals, scale checks)
  api.py                      FastAPI: /api/chat, /api/records/{ref}, /api/health

web/src/                      React + Vite + TypeScript, plain CSS
  App.tsx                     conversation state, composer, layout
  api.ts                      typed client for the FastAPI endpoints
  components/Answer.tsx       markdown answer; [V205] citations become clickable chips
  components/RecordPanel.tsx  the cited record, with extracted evidence highlighted
  components/Header.tsx       data freshness / backend status
  components/EmptyState.tsx   suggested questions
```

The UI is deliberately thin: one screen. Its job is to make the answer's evidence
inspectable: every citation opens the source record, and "How this was answered"
lists the tool calls behind the answer.

## Design decisions

**Data handling**
- **Source is read-only and stays where it is.** Opened with `mode=ro` via `SOURCE_DB_PATH`;
  no copy in the repo. Everything derived lives in `agent_index.db`, which is disposable.
- **"Today" comes from the data** (latest visit, 16 Nov 2025), so "this week" works on a
  historical sample. Override with `AS_OF`.
- **Safeguarding forms are linked by name, carefully.** Their `service_user_id` is NULL.
  Matching is scoped to the org unit; exact first, fuzzy only with a clear margin;
  ambiguous or weak matches go to a review queue instead of being guessed.

**Signal extraction (step 2)**
- One LLM call per visit, small model, strict Pydantic schema (structured output).
- The prompt includes that person's care-plan conditions and known risks, and handles
  negation ("no injury was noted"), hedging and escalation.
- **Names are replaced with `[SU]`** before text reaches the model (data minimisation).
- **The model is not trusted blindly:** every quote is checked against the source
  (unverified signals are excluded from scoring and flagged); care-plan matching and
  status contradictions are computed in code.
- Cached by `(visit, content hash, extractor)`: each visit is paid for once; re-runs only
  touch new or changed visits.
- A **keyword baseline extractor** runs offline. On this sample it finds all 17 risk visits
  (18 signals) with no false positives, but its patterns were written against this data,
  so it is a baseline and a test fixture, not the product.

**Risk scoring (step 3)**
- Weights low 1 / medium 2 / high 3. Immediate alert on any high-severity signal; trend
  alert when the 7-day sum reaches 6. On the sample the trend alert fires for Alfred and
  Doris on **13 Nov, two days before their first high-severity note**.
- Weights and threshold are illustrative (set on the same 14 days). In production they
  would be calibrated against outcomes and coordinators' acknowledge/dismiss decisions.

**Search (step 4)**
- One chunk per visit (a visit is short and is the unit you cite), one per form section.
- Hybrid: FTS5 BM25 with stemming + embeddings, fused with Reciprocal Rank Fusion.
- Vectors via sqlite-vec, partitioned by service user; pure-Python fallback when the
  extension can't load (common with macOS's built-in Python). Both are exact search,
  fine when filtered to one person.
- Search finds records; it is not how risk is detected. Similarity search can't count,
  loses time order, and matches "no concerns noted" for "concerns".

**Agent (step 5)**
- Plain tool-use loop on the OpenAI API, no framework, max 8 tool rounds.
- Tools are **bounded** (row caps), **read-only**, and return **citable refs**.
- `query_sql` is parsed with sqlglot: one SELECT, allow-listed tables, forced LIMIT,
  read-only connection, 5-second timeout.
- **Citation check:** every `[V205]` in the answer must have been returned by a tool in
  this turn; otherwise one correction round, then the citation is removed.
- The prompt forbids diagnosis and speculation about who is responsible in safeguarding.

## Testing and evaluation

Nothing in the test suite or the evaluation depends on the sample database. There are
four layers, and each one grows without anyone hand-labelling a fixed dataset:

| Layer | What it checks | Where | Labels come from |
|---|---|---|---|
| 1. Synthetic worlds | the whole pipeline: linking, rules, scores, alerts, tools, search | `backend/app/synthetic`, `backend/tests` | the generator: every risk sentence is planted |
| 2. Extraction cases | the LLM's judgement on phrasing patterns (negation, hedging, escalation, wording variety) | `eval/cases/extraction/*.yaml` | one small case per pattern, no database |
| 3. Agent questions | real answers to real questions | `eval/cases/agent.yaml`, `eval/checks.py` | computed from whatever data is loaded |
| 4. Invariants | properties that hold on any data | `backend/tests/test_invariants.py`, `checks.universal` | none needed |

**Synthetic worlds.** `app.synthetic` writes a database with the same schema as
`care_data.db` from scenarios: routine care, gradual decline over a week, single serious
events (with a safeguarding form that names the person but has no ID), negation traps
and documentation gaps. Because the risks are planted, the ground truth is known. The
pipeline runs with an *oracle* extractor that returns that truth, so a failing test
points at the logic under test, not at the extractor. Any size can be generated:
`python -m app.synthetic --people 2000`.

**Extraction cases** are self-contained visits with what must and must not be found.
Add one whenever a new phrasing, miss or false positive turns up. The keyword
baseline passes 25/38 (it can't generalise beyond its patterns); the LLM is scored on
the same cases.

**Agent questions** have no hard-coded answers. For example, "Has anyone had a fall?"
must cite every recorded high-severity fall in the period and must not say "no falls"
if any exist. "How is {name} doing?" is asked about the two highest-risk and two
calmest people and must cite only that person's records. So the same questions run on
the sample, a synthetic world of any size, or a production snapshot.

**Invariants** include: every scored quote appears verbatim in its source; every ref a
tool returns exists; unknown people aren't invented; tools respect their row caps; and
tool output stays the same size when the population grows from 200 to 1,000 people
(5,000 with `pytest -m slow`).

```bash
cd backend
pytest -q                                             # 61 tests, ~7 s, no API key, no sample DB needed
pytest -m slow                                        # + scale test at 5,000 people
python ../eval/run_eval.py extraction                 # keyword baseline + LLM on the pattern cases
python ../eval/run_eval.py agent                      # agent questions on your built data
python ../eval/run_eval.py agent --dataset synthetic --people 60
```

Results go to `eval/results/latest.md`; the exit code is non-zero on any failure, so
the same commands can gate prompt or model changes in CI. In production, labels would
also come from use: alerts coordinators acknowledge or dismiss become new cases.

## Scaling

Cost per question doesn't grow with the data: tools read pre-computed, indexed tables and
return bounded results (a risk question uses roughly 10-15K tokens of context whether there
are 300 or 3 million visits). Expensive LLM reading happens once per visit on the write path.

| | Prototype | Production |
|---|---|---|
| Store | SQLite (`agent_index.db`) | Postgres, partitioned by month, indexed on (org, service user, time) |
| Ingestion | Batch command, incremental | Change events → queue → workers; batch API for backfills |
| Edits to old records | Not detected (no `updated_at` in source) | CDC or `updated_at` |
| Vectors | sqlite-vec / Python, exact | pgvector with HNSW index |
| Roll-ups | Recomputed over a 30-day window | Same, triggered per affected person/day |
| Access control | Single tenant | Org-scoped rows (Postgres RLS), role-based tools |

## Known limitations

- Thresholds and the keyword baseline are tuned on a 14-day sample.
- Synthetic notes are template-based, so they test logic, not language variety; that is what the
  extraction cases are for.
- No streaming; answers take a few seconds with several tool calls.
