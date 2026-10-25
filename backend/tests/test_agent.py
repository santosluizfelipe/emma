"""Step 5: tools, SQL guard and the agent loop (with a scripted fake model)."""

import json
from types import SimpleNamespace

import pytest

from app import db
from app.agent import sql_guard
from app.agent.loop import Agent
from app.agent.tools import ToolContext, run_tool
from tests.conftest import needs_source

pytestmark = needs_source


@pytest.fixture()
def ctx(ro, pipeline):
    return ToolContext(conn=ro, as_of=db.get_as_of(ro), embedder=pipeline["embedder"])


# ---------------------------------------------------------------- tools

def test_risk_overview_ranks_the_two_deteriorations_first(ctx):
    people = run_tool(ctx, "risk_overview", {"days": 7})["people_with_signals"]
    assert [p["name"] for p in people[:2]] == ["Alfred Nkemelu", "Doris Whitfield"]
    assert people[0]["top_evidence"][0]["ref"] == "V205"


def test_find_service_user_by_first_name(ctx):
    matches = run_tool(ctx, "find_service_user", {"name": "doris"})["matches"]
    assert [m["service_user_id"] for m in matches] == [7]


def test_safeguarding_cases_are_linked(ctx):
    cases = run_tool(ctx, "safeguarding_cases", {})["cases"]
    assert {c["service_user"] for c in cases} == {"Raymond Clarke", "Iris Bennett", "Cynthia Ofori"}


def test_tool_errors_go_back_to_the_model(ctx):
    assert "error" in run_tool(ctx, "signal_timeline", {"service_user_id": 999})
    assert "error" in run_tool(ctx, "nope", {})


# ---------------------------------------------------------------- SQL guard

@pytest.mark.parametrize("sql", [
    "DELETE FROM visits",
    "UPDATE visits SET note = ''",
    "SELECT 1; SELECT 2",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM chunks_fts",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(visits)",
])
def test_sql_guard_rejects(sql):
    with pytest.raises(sql_guard.SQLRejected):
        sql_guard.validate(sql)


def test_sql_guard_caps_rows_and_allows_ctes():
    assert sql_guard.validate("SELECT * FROM visits").endswith("LIMIT 200")
    assert sql_guard.validate("SELECT * FROM visits LIMIT 5").endswith("LIMIT 5")
    sql_guard.validate("WITH x AS (SELECT * FROM visits) SELECT COUNT(*) FROM x")


def test_sql_runs_read_only(ctx):
    result = run_tool(ctx, "query_sql", {"sql": "SELECT COUNT(*) AS n FROM visits"})
    assert result["rows"] == [[336]]


# ---------------------------------------------------------------- agent loop

class ScriptedModel:
    """Plays back a list of responses: tool calls first, then a final answer."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, str):
            message = SimpleNamespace(content=step, tool_calls=None)
        else:
            message = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                for i, (name, args) in enumerate(step)
            ])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_agent_runs_tools_and_keeps_valid_citations(pipeline):
    model = ScriptedModel([
        [("risk_overview", {"days": 7})],
        "Alfred Nkemelu had a near-fall on 15 Nov [V205].",
    ])
    agent = Agent(client=model, model="fake", index_path=pipeline["path"], use_default_embedder=False)
    result = agent.ask("Who should I worry about?")
    assert result.answer.endswith("[V205].")
    assert [c["ref"] for c in result.citations] == ["V205"]
    assert result.tool_calls[0]["tool"] == "risk_overview"
    # the model received the tool result
    assert any(m.get("role") == "tool" for m in model.calls[-1]["messages"])


def test_agent_removes_invented_citations_after_one_correction(pipeline):
    model = ScriptedModel([
        [("find_service_user", {"name": "Margaret"})],
        "Margaret fell [V999].",          # V999 was never returned by a tool
        "Margaret fell [V999] again.",    # still wrong after the correction request
    ])
    agent = Agent(client=model, model="fake", index_path=pipeline["path"], use_default_embedder=False)
    result = agent.ask("Is Margaret OK?")
    assert "[V999]" not in result.answer
    assert result.removed_citations == ["V999"]
    assert "not returned by any tool" in model.calls[-1]["messages"][-1]["content"]


def test_grouped_citations_are_split_and_checked(pipeline):
    model = ScriptedModel([
        [("signal_timeline", {"service_user_id": 7})],
        "Doris became more breathless [V232, V247; V999].",
        "Doris became more breathless [V232, V247].",
    ])
    agent = Agent(client=model, model="fake", index_path=pipeline["path"], use_default_embedder=False)
    result = agent.ask("How is Doris?")
    assert "[V232] [V247]" in result.answer
    assert [c["ref"] for c in result.citations] == ["V232", "V247"]
