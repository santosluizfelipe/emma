# Care Data Agent

An agent that lets care managers ask questions about home-care visits and clinical
forms, and surfaces how service users are doing and where the risks are.

> Status: complete and evaluated. On 38 extraction cases the LLM extractor (gpt-5-mini)
> scores 38/38 against 25/38 for a keyword baseline. On the sample the agent answers in
> about 17 s (median) and passes its data-derived checks, apart from occasional loose quotes,
> which are caught and shown unquoted. A coordinator's question reads one team, so it costs
> the same at 1k or 16k people (see [Scaling](#scaling)).

## How to run it

### Prerequisites

- **Python 3.10+** (3.9 works)
- **Node.js 18+** and npm, for the front end
- The sample database **`care_data.db`** somewhere on your machine. It is not in this repo.
- An **OpenAI API key**, for LLM extraction, embeddings and the agent. Without one you can
  still build everything offline and run the tests, but not ask the agent questions.

### 1. Install the back end

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `backend/.env`:

| Setting | Required | Example |
|---|---|---|
| `SOURCE_DB_PATH` | yes | `/Users/you/data/care_data.db` (full path to the sample database) |
| `OPENAI_API_KEY` | for the agent | `sk-...` |
| `EXTRACTION_MODEL`, `AGENT_MODEL` | no | defaults `gpt-5-mini` and `gpt-5`; change if your key can't use them |
| `AGENT_ORG_UNIT_IDS` | no | `1` or `1,4`: the teams the agent may see. Empty = all data |
| `AGENT_REASONING_EFFORT` | no | default `low` (faster); `medium` quotes more exactly but takes about twice as long |

On macOS, avoid keeping the database in Downloads, Desktop or Documents: those
folders are privacy-protected and the terminal may be refused access.

### 3. Build the data

```bash
python -m app.ingest
```

This reads the sample database (read-only) and builds `backend/data/agent_index.db`:
it unpacks the JSON, has the LLM read each visit note once, computes risk scores and
builds the search index. It takes a few minutes for the sample and costs a few cents.
Re-running only processes new or changed records. An existing `agent_index.db` from an older
version is upgraded in place, reusing its stored LLM results and embeddings (no API calls).

No API key? Build offline instead (keyword extractor, keyword-only search), in seconds:

```bash
python -m app.ingest --extractor keyword --no-embeddings
```

### 4. Start the API

In the same terminal, and leave it running:

```bash
uvicorn app.api:app --reload --port 8000
```

Check http://localhost:8000/api/health: it should show `"ok": true` and `"visits": 336`.

### 5. Start the front end

In a second terminal:

```bash
cd web
npm install          # first time only
npm run dev
```

Open **http://localhost:5173**. The dev server forwards `/api` calls to port 8000.

### 6. Try it

The start screen already shows who needs attention this week, open safeguarding cases and
documentation gaps (read from the risk tables, no LLM). Then click a suggested question or
type your own:

- Who should I worry about this week?
- How is Doris Whitfield doing?
- Has anyone had a fall recently?
- What's outstanding on safeguarding?
- How many visits did each carer do last week?

Citation chips like **V205** (a visit) or **F11** (a form) open the source record on
the right, with the evidence highlighted. "How this was answered" lists the tool calls.

You can also ask from the terminal, without the front end:

```bash
cd backend && source .venv/bin/activate
python -m app.agent "Who should I worry about this week?"   # one question
python -m app.agent                                          # interactive
```

### Tests and evaluation

From `backend/` with the virtual environment active:

```bash
pytest -q                                           # 82 tests, ~5-10 s, offline
pytest -m slow                                      # + scale test at 5,000 people (~1.5 min)
python ../eval/run_eval.py extraction               # keyword vs LLM on 38 cases (~40 small LLM calls)
python ../eval/run_eval.py agent                    # agent questions on your built data (~5 min)
python ../eval/run_eval.py agent --dataset synthetic --people 60
python ../eval/run_eval.py agent --repeat 3         # consistency: pass rate + citation agreement (3x the cost)
```

Reports are written to `eval/results/`. See [Testing and evaluation](#testing-and-evaluation).

### Troubleshooting

| Problem | Fix |
|---|---|
| Tests show `skipped` with "sample database not found" | `SOURCE_DB_PATH` in `backend/.env` doesn't point at the file |
| `Cannot open ... privacy-protected` / `authorization denied` | Move the database out of Downloads/Desktop/Documents, or allow your terminal under System Settings → Privacy & Security → Files and Folders |
| `as_of not set: run ingestion first` | Run step 3 before starting the API |
| `model_not_found` | Set `EXTRACTION_MODEL` / `AGENT_MODEL` in `.env` to models your key can use |
| UI header says "Backend unavailable" | The API (step 4) isn't running on port 8000 |
| `503: OPENAI_API_KEY is not set` | Add the key to `backend/.env` and restart uvicorn |

Other useful commands: `python -m app.ingest.extract --max-visits 20` (cheap trial of the
LLM extractor), `python -m app.synthetic --people 1000` (generate a synthetic dataset).

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

## A note on scope

The brief suggested about 90 minutes. This repository goes further on purpose: the core
the brief asks for (agent, tools, retrieval, a light UI) is in `backend/app/agent`,
`backend/app/ingest`, `backend/app/search` and `web/`. The synthetic-data generator, the
evaluation harness, the scale tests and team scoping were added to check that the design
holds at production size, where the brief says it has to.

## Layout

```
backend/app/
  config.py, db.py, schema.sql, llm.py
  scope.py                    which records a user may see (their org units), as SQL filters
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
  api.py                      FastAPI: /api/chat, /api/briefing, /api/records/{ref}, /api/health

web/src/                      React + Vite + TypeScript, plain CSS
  App.tsx                     conversation state, composer, layout
  api.ts                      typed client for the FastAPI endpoints
  components/Answer.tsx       markdown answer; [V205] citations become clickable chips
  components/RecordPanel.tsx  the cited record, with extracted evidence highlighted
  components/Header.tsx       data freshness / backend status
  components/EmptyState.tsx   who needs attention (no LLM) + suggested questions
```

The UI is deliberately thin: one screen. Its job is to make the answer's evidence
inspectable: every citation opens the source record, and "How this was answered"
lists the tool calls behind the answer.

## Design decisions

**Data handling**
- **Source is read-only and stays where it is.** Opened with `mode=ro` via `SOURCE_DB_PATH`;
  no copy in the repo. Everything derived lives in `agent_index.db`, which is disposable.
- **"Today" comes from the data** (latest visit that has started, 16 Nov 2025), so "this week"
  works on a historical sample; future, scheduled visits don't move it. It is re-read for every
  question, so a new ingest shows up without a restart. Override with `AS_OF`.
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
  touch new or changed visits. Visits are paged from a per-extractor watermark, so a run
  reads only the visits after it (plus last run's failures), never the whole table again.
- A **keyword baseline extractor** runs offline. On this sample it finds all 17 risk visits
  (18 signals) with no false positives, but its patterns were written against this data,
  so it is a baseline and a test fixture, not the product.

**Risk scoring (step 3)**
- Weights low 1 / medium 2 / high 3. Immediate alert on any high-severity signal; trend
  alert when the 7-day sum reaches 6. On the sample the trend alert fires for Alfred and
  Doris on **13 Nov, two days before their first high-severity note**.
- Weights and threshold are illustrative (set on the same 14 days). In production they
  would be calibrated against outcomes and coordinators' acknowledge/dismiss decisions.
- **Incremental and sparse.** Normalise and extract queue what changed; rules re-check only
  those visits and people, and the roll-up recomputes only people whose signals changed plus
  any new days. Risk rows exist only for days with risk, so the table grows with risk, not
  with headcount.

**Search (step 4)**
- One chunk per visit (a visit is short and is the unit you cite), one per form section.
- Hybrid: FTS5 BM25 with stemming + embeddings, fused with Reciprocal Rank Fusion.
- Vectors via sqlite-vec, partitioned by service user with the org unit as a filter column;
  pure-Python fallback when the extension can't load (common with macOS's built-in Python),
  which refuses rather than scanning a sample when there are over 50,000 vectors. Both are
  exact search, fine per person or per team.
- Keyword rows carry a person token and a team token, so a person or team search reads only
  their postings instead of scoring everyone's matches.
- Incremental: records are paged from watermarks; only new or changed chunks are embedded
  and replaced in the vector index.
- Search finds records; it is not how risk is detected. Similarity search can't count,
  loses time order, and matches "no concerns noted" for "concerns".

**Agent (step 5)**
- Plain tool-use loop on the OpenAI API, no framework, max 8 tool rounds.
- Tools are **bounded** (row caps), **read-only**, and return **citable refs**.
- **Scope:** every tool is limited to the user's org units (teams), applied in the SQL itself,
  never by the model. The scope comes from `AGENT_ORG_UNIT_IDS` here, and from the login in
  production. A coordinator's question reads their team, not the whole store.
- **Capped lists say so:** every capped result returns `total`, `returned` and `truncated`, and
  the prompt requires answers like "the 10 highest of 433 people with alerts". Name lookups
  return `ambiguous` when several people match, and the agent asks rather than picks.
- `query_sql` is parsed with sqlglot: one SELECT, allow-listed tables (a CTE can't take a
  table's name), each table replaced by the rows in scope, forced LIMIT,
  read-only connection, 5-second timeout.
- **Citation check:** every `[V205]` in the answer must have been returned by a tool in
  this turn; otherwise one correction round, then the citation is removed.
- **Quote check:** words the answer puts in quotes must appear in a record cited on the same
  line; any that don't are shown under the answer as "check this quote". No extra model call.
- **Start screen:** who needs attention this week, open safeguarding cases and documentation
  gaps, read straight from the risk tables (`GET /api/briefing`): instant, free, the same
  every time, and shown before anyone types.
- Each question gets its own read-only connection, so concurrent requests don't share one.
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
Add one whenever a new phrasing, miss or false positive turns up.

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
pytest -q                                             # 82 tests, ~5-10 s, no API key needed
pytest -m slow                                        # + scale test at 5,000 people
python ../eval/run_eval.py extraction                 # keyword baseline + LLM on the pattern cases
python ../eval/run_eval.py agent                      # agent questions on your built data
python ../eval/run_eval.py agent --dataset synthetic --people 60
```

Results go to `eval/results/` (one file per run, plus `latest.md`); the exit code is
non-zero on any failure, so the same commands can gate prompt or model changes in CI.
In production, labels would also come from use: alerts coordinators acknowledge or
dismiss become new cases.

### Results

Run on 25 Oct 2026. Extraction model `gpt-5-mini`, agent model `gpt-5`.

| What | Result |
|---|---|
| Test suite | 83 tests: 82 with `pytest -q` (3 of them use the sample database and skip without it), plus the scale test with `-m slow` |
| Scale test (`pytest -m slow`) | passed: tool output the same size at 500 and 5,000 people |
| Extraction, keyword baseline | 25/38 cases; 8/8 negative cases clean |
| Extraction, LLM | **38/38 cases; 8/8 negative cases clean**, no invented quotes |
| Agent, sample, default reasoning effort | 9/10. Median 28 s (up to 50 s), 55 tool calls in all. The failure opened "No recorded falls" before reporting the near-fall |
| Agent, sample, `low` effort + prompt fixes | 7/10. Median 17 s (up to 22 s), 39 tool calls. The falls answer now opens "One near-fall recorded". All 3 failures are loose quotes: reworded text inside quotation marks, caught by the quote check |
| Agent, sample, `low` effort + exact-quote rule | 5/6 on the questions that had failed; 1 loose quote left |
| Cost | 6 agent questions: 51,779 tokens in, 9,776 out, about $0.16 |

What the numbers say:

- **The LLM extractor earns its cost.** The keyword baseline misses anything phrased
  differently from its patterns ("slipped in the bathroom", "tablets still in the
  blister pack", "quite wheezy", "takes his bank card"). The LLM caught all of these
  and still left every negated or reassuring note alone ("no injury was noted",
  "in no distress", "in good spirits").
- **Speed vs exact quotes.** `low` reasoning effort roughly halves answer time, but the
  model then sometimes rewords text inside quotation marks (0 such quotes at the default
  effort, 4 at `low`, 1 after adding an exact-quote rule to the prompt). The quote check
  catches every one and shows it without quotation marks, so a reader is never told a
  paraphrase is the carer's words. Set `AGENT_REASONING_EFFORT` to choose.

## Scaling

Cost per question doesn't grow with the data: tools read pre-computed, indexed tables and
return bounded results (a risk question uses roughly 10-15K tokens of context whether there
are 300 or 3 million visits). Expensive LLM reading happens once per visit on the write path.

Measured on synthetic data (14 days; the oracle extractor, so no API calls; people in teams
of 1,000; SQLite on a laptop). "Was" is the version before the incremental write path and
team scoping.

| | 1k people | 4k | 16k (465k visits) |
|---|---|---|---|
| Extraction pass over all visits | 0.5 s (was 1.7 s) | 1.8 s (was 22 s) | 7.5 s (was 356 s) |
| Re-run with no new data | 0.005 s (was 0.5 s) | 0.01 s (was 2.1 s) | 0.2 s (was 9.1 s) |
| Name lookup, one team | 23 ms | 22 ms | 26 ms (whole store: 390 ms) |
| Risk overview, one team | 0.6 ms | 0.7 ms | 0.7 ms (was 69 ms) |
| Risk table rows | 836 | 3,455 | 13,570 (was 496,000) |

Re-measured on a second machine at 1k and 4k people: the absolute times differ, the shape
is the same (for 4x the people, extraction took 12x longer before and 3.8x after; a re-run
with no new data, a name lookup and a risk overview for one team stayed flat).

A coordinator's question costs the same at every size because it reads one team. With the
real extractor, a visit costs one small LLM call (the sample's 336 visits took 87 s at 8 in
parallel), paid once. The first build's normalise step still grows a little faster than
linear (0.8 s, 4.2 s, 29 s): SQLite writing index pages as the file grows. That is a
one-time backfill; production would bulk-load Postgres.

| | Prototype | Production |
|---|---|---|
| Store | SQLite (`agent_index.db`) | Postgres, partitioned by month, indexed on (org, service user, time) |
| Ingestion | Batch command; watermarks + change queues, so a run does work in proportion to what changed | Change events → queue → workers; batch API for backfills |
| Edits to old records | Not detected (no `updated_at` in source) | CDC or `updated_at` |
| Vectors | sqlite-vec (filtered by person or team) / Python, exact | pgvector with HNSW index |
| Roll-ups | Sparse; recomputed for people whose signals changed, plus new days | Same |
| Access control | Org-unit scope in every tool's SQL, set by config | Same, set from the login; plus Postgres RLS, role-based tools |

## Known limitations

- **Latency:** about 17 s per answer (median) with `gpt-5` at `low` effort; answers aren't
  streamed yet. The start screen (who needs attention) is instant because it uses no model.
- The first build's normalise step grows a little faster than linear (SQLite index writes);
  later runs only touch new records.
- Thresholds and severity weights are illustrative, set on a 14-day sample.
- The extraction cases are written by hand (38 so far); they cover patterns, not the
  full range of how carers write.
- Synthetic notes are template-based, so they test logic, not language variety; that is
  what the extraction cases are for.
- The source has no `updated_at`, so edits to existing visits aren't re-ingested.
- No authentication: the scope comes from configuration (`AGENT_ORG_UNIT_IDS`), not a login.

## Next steps

1. **Stream answers** to cut perceived latency; try `gpt-5-mini` for the agent with the eval
   as the gate (`python ../eval/run_eval.py agent --repeat 3` also measures consistency).
2. **Alerts with acknowledge/dismiss** on the start screen. Those decisions become labelled
   cases and calibrate the thresholds.
3. **Name lookup through an index** (it scans the user's team in Python: fine for a team,
   not for an organisation-wide search).
4. **Production path:** Postgres + pgvector (HNSW), queue-based extraction workers on the
   Batch API, scope from the login, audit log of questions, tool calls and answers.
