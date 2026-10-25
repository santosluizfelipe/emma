"""Finding people by name."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.agent.tools.context import ToolContext, page_info
from app.ingest.linking import normalise_name

EXACT, PARTIAL, FUZZY_MIN = 1.0, 0.95, 0.6


def find_service_user(ctx: ToolContext, name: str) -> dict:
    """Resolve a name or partial name to service user IDs, within the user's scope."""
    target = normalise_name(name)
    in_scope, params = ctx.people_filter("id")
    people = ctx.conn.execute(
        f"SELECT id, name, date_of_birth, status FROM service_users WHERE {in_scope}", params
    ).fetchall()  # the people in scope (a team), not the whole store
    scored = []
    for p in people:
        full = normalise_name(p["name"])
        parts = full.split()
        if target and target == full:
            score = EXACT
        elif target and (target in parts or full.startswith(target)):
            score = PARTIAL  # first name, surname or prefix
        else:
            score = max([SequenceMatcher(None, target, full).ratio()] +
                        [SequenceMatcher(None, target, part).ratio() for part in parts])
        scored.append((score, p))
    best = max((score for score, _ in scored), default=0.0)
    if best >= PARTIAL:  # clear match(es): don't pad with look-alikes
        candidates = [pair for pair in scored if pair[0] == best]
    else:
        candidates = [pair for pair in scored if pair[0] >= FUZZY_MIN]
    candidates.sort(key=lambda pair: (-pair[0], pair[1]["name"], pair[1]["id"]))
    matches = [
        {"service_user_id": p["id"], "name": p["name"], "date_of_birth": p["date_of_birth"],
         "status": p["status"], "match_score": round(score, 2)}
        for score, p in candidates[:5]
    ]
    return {"query": name, "matches": matches, "ambiguous": len(candidates) > 1,
            **page_info(len(candidates), len(matches))}


SPECS = {
    "find_service_user": {
        "fn": find_service_user,
        "description": "Resolve a person's name (or first name / nickname) to service_user_id. Call before person-specific tools. "
                       "If ambiguous is true, ask the user which person they mean (name and date of birth); don't pick one.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
}
