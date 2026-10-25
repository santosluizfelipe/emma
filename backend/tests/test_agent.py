"""Step 5: tools, SQL guard and the agent loop (scripted fake model)."""

import json
from types import SimpleNamespace

import pytest

from app import db
from app.agent import sql_guard
from app.agent.loop import Agent
from app.agent.tools import ToolContext, run_tool
from tests.conftest import person


@pytest.fixture()
def ctx(ro, world):
    return ToolContext(conn=ro, as_of=db.get_as_of(ro), embedder=world["embedder"])


# ---------------------------------------------------------------- tools

def test_risk_overview_ranks_current_alerts_first(ctx, ro):
    people = run_tool(ctx, "risk_overview", {"days": 7})["people_with_signals"]
    expected = [r[0] for r in ro.execute(
        "SELECT service_user_id FROM su_daily_risk WHERE day = ? ORDER BY rolling_7d DESC LIMIT 2", (ctx.today,))]
    assert {p["service_user_id"] for p in people[:2]} == set(expected)
    assert all(p["top_evidence"] for p in people)


def test_find_service_user_by_first_name(ctx, truth):
    su = person(truth, "money")
    first = truth.people[su]["name"].split()[0]
    assert su in [m["service_user_id"] for m in run_tool(ctx, "find_service_user", {"name": first})["matches"]]


def test_safeguarding_cases_are_linked(ctx, truth):
    cases = run_tool(ctx, "safeguarding_cases", {})["cases"]
    assert {int(c["ref"][1:]): c["service_user_id"] for c in cases} == truth.safeguarding_links


def test_tool_errors_go_back_to_the_model(ctx):
    assert "error" in run_tool(ctx, "signal_timeline", {"service_user_id": 99999})
    assert "error" in run_tool(ctx, "nope", {})


# ---------------------------------------------------------------- SQL guard

@pytest.mark.parametrize("sql", [
    "DELETE FROM visits", "UPDATE visits SET note = ''", "SELECT 1; SELECT 2", "SELECT * FROM sqlite_master",
    "SELECT * FROM chunks_fts", "ATTACH DATABASE 'x.db' AS x", "PRAGMA table_info(visits)",
])
def test_sql_guard_rejects(sql):
    with pytest.raises(sql_guard.SQLRejected):
        sql_guard.validate(sql)


def test_sql_guard_caps_rows_and_allows_ctes():
    assert sql_guard.validate("SELECT * FROM visits").endswith("LIMIT 200")
    assert sql_guard.validate("SELECT * FROM visits LIMIT 5").endswith("LIMIT 5")
    sql_guard.validate("WITH x AS (SELECT * FROM visits) SELECT COUNT(*) FROM x")


def test_sql_runs_read_only(ctx, ro):
    n = ro.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    assert run_tool(ctx, "query_sql", {"sql": "SELECT COUNT(*) AS n FROM visits"})["rows"] == [[n]]


# ---------------------------------------------------------------- agent loop

class ScriptedModel:
    """Plays back tool calls, then answers. Strings are answers; lists are tool calls."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, str):
            message = SimpleNamespace(content=step, tool_calls=None)
        else:
            message = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
                for i, (n, a) in enumerate(step)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _agent(world, script):
    model = ScriptedModel(script)
    return Agent(client=model, model="fake", index_path=world["index"], use_default_embedder=False), model


def test_agent_runs_tools_and_keeps_valid_citations(world, truth):
    top = next(s for s in truth.signals if s.severity == "high")
    agent, model = _agent(world, [[("risk_overview", {"days": 14})], f"A serious event was recorded [V{top.visit_id}]."])
    result = agent.ask("Who should I worry about?")
    assert [c["ref"] for c in result.citations] == [f"V{top.visit_id}"]
    assert any(m.get("role") == "tool" for m in model.calls[-1]["messages"])


def test_agent_removes_invented_citations_after_one_correction(world):
    agent, model = _agent(world, [[("find_service_user", {"name": "x"})], "Fell [V99999].", "Fell [V99999] again."])
    result = agent.ask("Anything?")
    assert "[V99999]" not in result.answer and result.removed_citations == ["V99999"]
    assert "not returned by any tool" in model.calls[-1]["messages"][-1]["content"]


def test_grouped_citations_are_split_and_checked(world, truth):
    su = person(truth, "gradual_decline")
    a, b = [f"V{s.visit_id}" for s in truth.signals_for(su)[:2]]
    agent, _ = _agent(world, [[("signal_timeline", {"service_user_id": su})], f"Declining [{a}, {b}; V99999].",
                              f"Declining [{a}, {b}]."])
    result = agent.ask("How are they?")
    assert f"[{a}] [{b}]" in result.answer
