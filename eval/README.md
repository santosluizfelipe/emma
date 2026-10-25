# Evaluation

Tests (`backend/tests`) check that the code does what it should. This folder measures
how good the LLM's judgements are. Neither depends on the sample database.

| Layer | Where | Labels come from | Grows by |
|---|---|---|---|
| 1. Synthetic worlds | `backend/app/synthetic` | the generator (planted scenarios) | new scenarios, any population size |
| 2. Extraction cases | `cases/extraction/*.yaml` | a person, once per pattern | adding a case per new phrasing, miss or false positive |
| 3. Agent questions | `cases/agent.yaml` + `checks.py` | computed from the loaded data | adding a question + check |
| 4. Invariants | `checks.universal`, `backend/tests/test_invariants.py` | none needed | adding a property |

```bash
cd backend && source .venv/bin/activate
python ../eval/run_eval.py extraction                 # keyword (free) + LLM (~40 small calls)
python ../eval/run_eval.py agent                      # your data, ~10 agent questions
python ../eval/run_eval.py agent --dataset synthetic --people 60
python ../eval/run_eval.py all
```

Results: `results/latest.md` and `latest.json`. Exit code 1 on any failure, so the
same command can gate a prompt or model change in CI.

In production, labels would also come from use: every alert a coordinator
acknowledges or dismisses becomes a case, and a sample of routine notes is reviewed
each week to catch misses.
