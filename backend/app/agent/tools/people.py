"""Finding people by name."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.agent.tools.context import ToolContext
from app.ingest.linking import normalise_name


def find_service_user(ctx: ToolContext, name: str) -> dict:
    """Resolve a name or partial name to service user IDs."""
    target = normalise_name(name)
    people = ctx.conn.execute(
        "SELECT id, name, date_of_birth, status FROM service_users"
    ).fetchall()  # reference table: thousands of rows at most
    scored = []
    for p in people:
        full = normalise_name(p["name"])
        parts = full.split()
        if target and (target == full or target in parts or full.startswith(target)):
            score = 1.0
        else:
            score = max([SequenceMatcher(None, target, full).ratio()] +
                        [SequenceMatcher(None, target, part).ratio() for part in parts])
        scored.append((score, p))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] == 1.0:  # clear match(es): don't pad with look-alikes
        scored = [pair for pair in scored if pair[0] == 1.0]
    matches = [
        {"service_user_id": p["id"], "name": p["name"], "date_of_birth": p["date_of_birth"],
         "status": p["status"], "match_score": round(score, 2)}
        for score, p in scored[:5] if score >= 0.6
    ]
    return {"query": name, "matches": matches}


SPECS = {
    "find_service_user": {
        "fn": find_service_user,
        "description": "Resolve a person's name (or first name / nickname) to service_user_id. Call before person-specific tools.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
}
