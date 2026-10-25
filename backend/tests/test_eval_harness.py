"""The evaluation harness itself (eval/): case files are valid, scoring is right,
and data-derived agent checks pass good answers and fail bad ones."""

import sys
from pathlib import Path

import pytest

from app import db

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
import agent_eval  # noqa: E402
import checks  # noqa: E402
import extraction_eval  # noqa: E402
from tests.conftest import person  # noqa: E402


# ---------------------------------------------------------------- layer 2: extraction cases

def test_all_extraction_cases_are_valid():
    cases = extraction_eval.load_cases()
    assert len(cases) >= 30
    assert any(not c["expect"] for c in cases), "need negative cases"


def test_case_scoring():
    case = {"id": "x", "note": "n", "expect": [{"category": ["skin_injury", "safeguarding"], "severity": "high"}],
            "forbid": ["mood"]}
    sig = lambda c, s="high": {"category": c, "severity": s, "escalated": False, "evidence": "e", "verbatim": True}  # noqa: E731
    assert extraction_eval.score_case(case, [sig("safeguarding")]) == []
    assert extraction_eval.score_case(case, [sig("skin_injury", "low")])  # wrong severity
    assert extraction_eval.score_case(case, [])  # missed
    assert extraction_eval.score_case(case, [sig("skin_injury"), sig("mood")])  # forbidden
    negative = {"id": "n", "note": "n", "expect": []}
    assert extraction_eval.score_case(negative, [sig("mood")])


def test_bad_case_files_are_rejected():
    with pytest.raises(ValueError):
        extraction_eval.validate_case({"id": "x", "note": "n", "expect": [{"category": "nonsense"}]})


# ---------------------------------------------------------------- layer 3: data-derived agent checks

@pytest.fixture()
def env(ro, truth):
    return ro, db.get_as_of(ro), truth


def test_agent_cases_are_valid_and_expand_per_person(env):
    conn, as_of, _ = env
    expanded = agent_eval.expand(agent_eval.load_cases(), conn, as_of)
    assert sum(c["id"].startswith("person:") for c in expanded) == 4


def test_person_summary_check(env):
    conn, as_of, truth = env
    su = person(truth, "gradual_decline")
    high = next(s for s in truth.signals_for(su) if s.severity == "high")
    other = next(s for s in truth.signals if s.service_user_id != su)
    case = {"service_user_id": su}
    assert checks.person_summary("Serious event [V%d]." % high.visit_id, [f"V{high.visit_id}"], [], conn, as_of, case) == []
    assert checks.person_summary("Fine.", [f"V{other.visit_id}"], [], conn, as_of, case)  # someone else's record
    calm = person(truth, "routine")
    assert checks.person_summary("Settled, no concerns.", [], [], conn, as_of, {"service_user_id": calm}) == []
    assert checks.person_summary("Worrying decline.", [], [], conn, as_of, {"service_user_id": calm})


def test_falls_check(env):
    conn, as_of, truth = env
    fall = next(s for s in truth.signals if s.category == "falls_mobility" and s.severity == "high")
    good = f"Yes: a fall was recorded [V{fall.visit_id}]."
    assert checks.falls(good, [f"V{fall.visit_id}"], [], conn, as_of, {}) == []
    assert checks.falls("There were no falls this fortnight.", [], [], conn, as_of, {})


def test_alerted_people_check(env):
    conn, as_of, truth = env
    names = " and ".join(p["name"] for p in truth.people.values())
    assert checks.alerted_people(names + " [V1]", ["V1"], ["risk_overview"], conn, as_of, {"top": 3}) == []
    assert checks.alerted_people("Nobody.", [], [], conn, as_of, {"top": 3})


def test_safeguarding_and_count_checks(env):
    conn, as_of, truth = env
    refs = [f"F{f}" for f in truth.safeguarding_links]
    assert checks.safeguarding(" ".join(f"[{r}]" for r in refs), refs, ["safeguarding_cases"], conn, as_of, {}) == []
    n = len(truth.completed_without_comment)
    assert checks.completed_without_comment(f"{n} tasks.", [], [], conn, as_of, {}) == []
    assert checks.completed_without_comment(f"{n + 1} tasks.", [], [], conn, as_of, {})


def test_carer_counts_and_unknown_person_checks(env):
    conn, as_of, _ = env
    rows = conn.execute("SELECT c.name, COUNT(*) FROM visits v JOIN carers c ON c.id = v.carer_id "
                        "WHERE v.start_time >= ? GROUP BY c.name", (checks.days_ago(as_of, 7),)).fetchall()
    table = "\n".join(f"- {name}: {n}" for name, n in rows)
    assert checks.carer_counts(table, [], ["query_sql"], conn, as_of, {}) == []
    assert checks.unknown_person("I couldn't find anyone with that name.", [], [], conn, as_of, {}) == []
    assert checks.unknown_person("I can\u2019t find any records for that name.", [], [], conn, as_of, {}) == []
    assert checks.unknown_person("They are fine [V1].", ["V1"], [], conn, as_of, {})


def test_universal_invariants(env):
    conn, _, _ = env
    assert checks.universal("ok [V1]", ["V1"], [], conn) == []
    assert checks.universal("ok [V99999]", ["V99999"], [], conn)
    assert checks.universal("ok", [], ["V5"], conn)


def test_stability_summary_over_repeated_runs():
    import run_eval
    runs = [{"id": "worry", "passed": True, "cited": ["V1", "V2"]},
            {"id": "worry", "passed": False, "cited": ["V1", "V3"]}]
    assert run_eval.stability(runs) == [{"id": "worry", "runs": 2, "passed": 1, "cited_agreement": 0.33}]
