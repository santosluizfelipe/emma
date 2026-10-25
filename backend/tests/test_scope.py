"""Scope and totals: a coordinator sees their team only, and a capped list says it's capped.

Runs on a synthetic world with two org units (odd service user ids in unit 1, even in unit 2).
"""

import json
import re
import sqlite3

import pytest

from app import db
from app.agent import sql_guard
from app.agent.tools import ToolContext, run_tool
from app.search.embeddings import HashEmbedder
from app.synthetic.builder import standard_scenarios
from app.synthetic.pipeline import build_world


@pytest.fixture(scope="module")
def two_teams(tmp_path_factory):
    embedder = HashEmbedder()
    built = build_world(tmp_path_factory.mktemp("two_teams"), standard_scenarios(), embedder=embedder, org_units=2)
    conn = db.connect_index_readonly(built["index"])
    yield {**built, "conn": conn, "embedder": embedder}
    conn.close()


def _ctx(world, org_units):
    conn = world["conn"]
    return ToolContext(conn=conn, as_of=db.get_as_of(conn), embedder=world["embedder"], org_unit_ids=org_units)


def _ids(conn, sql, *params):
    return {r[0] for r in conn.execute(sql, params)}


def _orgwide_calls():
    return [
        ("risk_overview", {"days": 14, "limit": 25}),
        ("record_issues", {"days": 30}),
        ("safeguarding_cases", {}),
        ("search_records", {"query": "breathless bruising money medication fall confused", "k": 15}),
        ("query_sql", {"sql": "SELECT id AS service_user_id, name FROM service_users"}),
        ("query_sql", {"sql": "SELECT v.id AS visit_id, v.service_user_id FROM visits v JOIN visit_signals s ON s.visit_id = v.id"}),
        ("query_sql", {"sql": "WITH s AS (SELECT * FROM visits) SELECT service_user_id FROM s"}),
    ]


def test_orgwide_tools_only_return_the_users_team(two_teams):
    conn = two_teams["conn"]
    team = _ids(conn, "SELECT id FROM service_users WHERE org_unit_id = 1")
    team_visits = _ids(conn, "SELECT id FROM visits WHERE service_user_id IN (SELECT id FROM service_users WHERE org_unit_id = 1)")
    team_forms = _ids(conn, "SELECT id FROM forms WHERE org_unit_id = 1")
    ctx = _ctx(two_teams, (1,))
    for tool, args in _orgwide_calls():
        result = run_tool(ctx, tool, args)
        assert "error" not in result, (tool, result)
        payload = json.dumps(result)
        assert set(int(x) for x in re.findall(r'"service_user_id": (\d+)', payload)) <= team, tool
        assert {int(r[1:]) for r in re.findall(r"\bV\d+\b", payload)} <= team_visits, tool
        assert {int(r[1:]) for r in re.findall(r"\bF\d+\b", payload)} <= team_forms, tool
        if tool == "query_sql":
            col = result["columns"].index("service_user_id")
            assert result["rows"] and {row[col] for row in result["rows"]} <= team, args


def test_person_tools_refuse_someone_from_another_team(two_teams):
    conn = two_teams["conn"]
    other = conn.execute("SELECT id, name FROM service_users WHERE org_unit_id = 2 LIMIT 1").fetchone()
    other_visit = conn.execute("SELECT id FROM visits WHERE service_user_id = ?", (other["id"],)).fetchone()[0]
    ctx = _ctx(two_teams, (1,))
    for tool in ("signal_timeline", "get_care_plan", "get_visit_notes", "record_issues", "safeguarding_cases"):
        assert "error" in run_tool(ctx, tool, {"service_user_id": other["id"]}), tool
    assert "error" in run_tool(ctx, "search_records", {"query": "breakfast", "service_user_id": other["id"]})
    assert run_tool(ctx, "get_visit_notes", {"visit_refs": [f"V{other_visit}"]})["visits"] == []
    assert run_tool(ctx, "find_service_user", {"name": other["name"]})["matches"] == []


def test_teams_add_up_to_the_whole(two_teams):
    def totals(org_units):
        ctx = _ctx(two_teams, org_units)
        risk = run_tool(ctx, "risk_overview", {"days": 14, "limit": 1})
        visits = run_tool(ctx, "query_sql", {"sql": "SELECT COUNT(*) FROM visits"})["rows"][0][0]
        cases = run_tool(ctx, "safeguarding_cases", {})["total"]
        return risk["total"], risk["alerting_in_period"]["any_alert"], visits, cases

    one, two, everyone = totals((1,)), totals((2,)), totals(None)
    assert all(a > 0 and b > 0 for a, b in zip(one, two))
    assert tuple(a + b for a, b in zip(one, two)) == everyone
    assert totals(()) == (0, 0, 0, 0)  # no teams: nothing


def test_capped_lists_report_their_totals(two_teams):
    ctx = _ctx(two_teams, None)
    risk = run_tool(ctx, "risk_overview", {"days": 14, "limit": 1})
    assert risk["returned"] == 1 and risk["truncated"] and risk["total"] > 1
    assert risk["people_with_no_signals"] == risk["service_users_in_scope"] - risk["total"]
    cases = run_tool(ctx, "safeguarding_cases", {})
    assert cases["total"] == len(two_teams["truth"].safeguarding_links) == cases["returned"]
    assert not cases["truncated"]
    issues = run_tool(ctx, "record_issues", {"days": 30})
    assert issues["total"] == sum(s["items"] for s in issues["summary"]) >= issues["returned"]


def test_risk_overview_order_is_stable(two_teams):
    ctx = _ctx(two_teams, None)
    runs = [[p["service_user_id"] for p in run_tool(ctx, "risk_overview", {"days": 14, "limit": 25})["people_with_signals"]]
            for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_find_service_user_prefers_exact_name_and_flags_ambiguity():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE service_users (id INTEGER PRIMARY KEY, name TEXT, date_of_birth TEXT, status TEXT, org_unit_id INTEGER)")
    conn.executemany("INSERT INTO service_users VALUES (?, ?, '1940-01-01', 'Active', 1)",
                     [(1, "Walter Timms"), (2, "Walter Timmsworth"), (3, "Walter Smith"), (4, "Ann Lee")])
    ctx = ToolContext(conn=conn, as_of="2025-11-16 00:00:00")
    exact = run_tool(ctx, "find_service_user", {"name": "Walter Timms"})
    assert [m["service_user_id"] for m in exact["matches"]] == [1] and not exact["ambiguous"]
    first_name = run_tool(ctx, "find_service_user", {"name": "Walter"})
    assert first_name["total"] == 3 and first_name["ambiguous"]


@pytest.mark.parametrize("sql", [
    "WITH meta AS (SELECT * FROM meta) SELECT * FROM meta",
    "WITH sqlite_master AS (SELECT * FROM sqlite_master) SELECT name FROM sqlite_master",
    "WITH a AS (SELECT * FROM visits), chunks AS (SELECT * FROM chunks) SELECT * FROM a",
    "WITH visits AS (SELECT * FROM visits) SELECT * FROM visits",
    # would make the scope filter read a fake service_users with another team's ids
    "WITH service_users AS (SELECT 2 AS id, 1 AS org_unit_id) SELECT * FROM visits",
])
def test_sql_guard_cte_cannot_borrow_a_table_name(sql):
    with pytest.raises(sql_guard.SQLRejected):
        sql_guard.validate(sql, org_unit_ids=(1,))
