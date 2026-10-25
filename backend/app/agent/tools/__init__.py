"""The agent's tools: the ONLY way the model sees data.

Every tool is:
  * bounded   - row caps and summaries, so a result never grows with the database
  * read-only - runs on a read-only connection to the derived store
  * citable   - returns record refs (V205 = visit 205, F11 = form 11) the answer must cite

Dates default to a window ending at "as of" (the latest data), not the real clock.

Each module defines its tools next to their schemas (SPECS); this file merges them
into the registry the agent loop uses.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.agent import sql_guard
from app.agent.tools import people, records, risk, search, sql
from app.agent.tools.context import ToolContext
from app.agent.tools.people import find_service_user
from app.agent.tools.records import get_care_plan, get_visit_notes, safeguarding_cases
from app.agent.tools.risk import record_issues, risk_overview, signal_timeline
from app.agent.tools.search import search_records
from app.agent.tools.sql import query_sql

TOOLS: Dict[str, Dict[str, Any]] = {
    **people.SPECS,
    **risk.SPECS,
    **records.SPECS,
    **search.SPECS,
    **sql.SPECS,
}

__all__ = [
    "TOOLS", "ToolContext", "openai_tool_specs", "run_tool",
    "find_service_user", "risk_overview", "signal_timeline", "record_issues",
    "get_visit_notes", "get_care_plan", "safeguarding_cases", "search_records", "query_sql",
]


def openai_tool_specs() -> List[dict]:
    return [
        {"type": "function", "function": {"name": name, "description": spec["description"],
                                          "parameters": spec["parameters"]}}
        for name, spec in TOOLS.items()
    ]


def run_tool(ctx: ToolContext, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool call from the model. Errors are returned to the model, not raised."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool {name}"}
    try:
        fn: Callable[..., dict] = spec["fn"]
        allowed = set(spec["parameters"].get("properties", {}))
        return fn(ctx, **{k: v for k, v in (arguments or {}).items() if k in allowed and v is not None})
    except (sql_guard.SQLRejected, ValueError, TypeError) as exc:
        return {"error": str(exc)}
