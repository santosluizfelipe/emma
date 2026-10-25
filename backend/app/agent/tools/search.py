"""Hybrid keyword + vector search over notes and form sections."""

from __future__ import annotations

from typing import Optional

from app.agent.tools.context import OPT_DATE, ToolContext
from app.search import hybrid


def search_records(ctx: ToolContext, query: str, service_user_id: Optional[int] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   record_type: Optional[str] = None, k: int = 8) -> dict:
    """Find visit notes and form sections by wording or meaning (hybrid keyword + vector)."""
    k = max(1, min(k, 15))
    results = hybrid.search(ctx.conn, query, embedder=ctx.embedder, service_user_id=service_user_id,
                            date_from=date_from, date_to=date_to,
                            record_type=record_type if record_type in ("visit", "form") else None, k=k)
    for r in results:
        r["text"] = r["text"][:800]
    return {
        "query": query,
        "retrievers": ["keyword"] + (["vector"] if ctx.embedder else []),
        "results": results,
        "warning": "Search finds wording, including negated mentions ('no concerns'). Read the text before concluding.",
    }


SPECS = {
    "search_records": {
        "fn": search_records,
        "description": "Search visit notes and form sections by wording or meaning (hybrid keyword + semantic). Use for specific terms or topics not covered by signals, e.g. 'inhaler', 'family visit', 'GP'.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "service_user_id": {"type": "integer"},
            "date_from": OPT_DATE, "date_to": OPT_DATE,
            "record_type": {"type": "string", "enum": ["visit", "form"]},
            "k": {"type": "integer", "description": "results, default 8"}},
            "required": ["query"]},
    },
}
