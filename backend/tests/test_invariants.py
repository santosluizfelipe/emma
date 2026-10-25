"""Layer 4: properties that must hold on ANY data, no labels needed.

Run on the standard synthetic world, and the scale test on generated populations.
Run the large version with:  pytest -m slow
"""

import json

import pytest

from app import db
from app.agent.tools import ToolContext, run_tool
from app.ingest.extract import _squash
from app.synthetic.builder import demo_population
from app.synthetic.pipeline import build_world


def test_every_scored_signal_quotes_its_source_verbatim(ro):
    rows = ro.execute(
        "SELECT s.evidence, v.note, (SELECT group_concat(comment, ' ') FROM visit_activities a WHERE a.visit_id = v.id) AS comments "
        "FROM visit_signals s JOIN visits v ON v.id = s.visit_id WHERE s.evidence_verified = 1").fetchall()
    assert rows
    for r in rows:
        assert _squash(r["evidence"]) in _squash(f"{r['note']} {r['comments'] or ''}")


def test_every_ref_a_tool_returns_exists(ro):
    ctx = ToolContext(conn=ro, as_of=db.get_as_of(ro))
    payload = json.dumps([run_tool(ctx, "risk_overview", {"days": 14}), run_tool(ctx, "safeguarding_cases", {}),
                          run_tool(ctx, "record_issues", {"days": 30})])
    import re
    for ref in set(re.findall(r'"(V\d+|F\d+)"', payload)):
        table = "visits" if ref[0] == "V" else "forms"
        assert ro.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(ref[1:]),)).fetchone(), ref


def test_unknown_person_returns_no_match(ro):
    ctx = ToolContext(conn=ro, as_of=db.get_as_of(ro))
    assert run_tool(ctx, "find_service_user", {"name": "Zebedee Quuxworth"})["matches"] == []


def test_tool_row_caps(ro):
    ctx = ToolContext(conn=ro, as_of=db.get_as_of(ro))
    assert len(run_tool(ctx, "get_visit_notes", {"service_user_id": 1, "limit": 500, "date_from": "2000-01-01"})["visits"]) <= 20
    assert len(run_tool(ctx, "risk_overview", {"limit": 500})["people_with_signals"]) <= 25
    assert run_tool(ctx, "query_sql", {"sql": "SELECT * FROM visit_activities"})["row_count"] <= 200


def _tool_sizes(folder, n):
    w = build_world(folder, demo_population(n, seed=3))
    conn = db.connect_index_readonly(w["index"])
    ctx = ToolContext(conn=conn, as_of=db.get_as_of(conn))
    su = conn.execute("SELECT service_user_id FROM su_daily_risk ORDER BY rolling_7d DESC LIMIT 1").fetchone()[0]
    sizes = {
        "risk_overview": len(json.dumps(run_tool(ctx, "risk_overview", {"days": 7, "limit": 10}))),
        "signal_timeline": len(json.dumps(run_tool(ctx, "signal_timeline", {"service_user_id": su}))),
        "record_issues": len(json.dumps(run_tool(ctx, "record_issues", {}))),
        "search": len(json.dumps(run_tool(ctx, "search_records", {"query": "breathless", "k": 8}))),
    }
    conn.close()
    return sizes


# Absolute ceilings implied by the tools' row caps (characters of JSON)
CEILINGS = {"risk_overview": 40_000, "signal_timeline": 25_000, "record_issues": 15_000, "search": 15_000}


def _assert_flat(small, large):
    for tool, size in large.items():
        assert size <= CEILINGS[tool], (tool, size)
        # once results are saturated (caps reached), 5x more people must not mean more output
        assert size <= small[tool] * 1.3, (tool, small[tool], size)


def test_tool_output_size_is_flat_as_data_grows(tmp_path):
    _assert_flat(_tool_sizes(tmp_path / "s", 200), _tool_sizes(tmp_path / "l", 1000))


@pytest.mark.slow
def test_tool_output_size_is_flat_at_thousands_of_people(tmp_path):
    _assert_flat(_tool_sizes(tmp_path / "s", 500), _tool_sizes(tmp_path / "l", 5000))
