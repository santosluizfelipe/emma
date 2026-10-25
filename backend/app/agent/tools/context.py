"""What every tool receives, plus small shared helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Tuple

from app import db, scope
from app.search.embeddings import Embedder

OPT_DATE = {"type": "string", "description": "YYYY-MM-DD"}


@dataclass
class ToolContext:
    conn: sqlite3.Connection        # read-only connection to the derived store
    as_of: str                      # "now" for this dataset
    embedder: Optional[Embedder] = None
    org_unit_ids: scope.OrgUnits = None  # set by the server, never by the model; None = everything

    @property
    def today(self) -> str:
        return db.day_of(self.as_of)

    def days_ago(self, days: int) -> str:
        return (date.fromisoformat(self.today) - timedelta(days=days - 1)).isoformat()

    def people_filter(self, column: str) -> Tuple[str, List[int]]:
        return scope.people_filter(column, self.org_unit_ids)

    def forms_filter(self, column: str) -> Tuple[str, List[int]]:
        return scope.forms_filter(column, self.org_unit_ids)


def person_name(ctx: ToolContext, su_id: Optional[int]) -> Optional[str]:
    if su_id is None:
        return None
    row = ctx.conn.execute("SELECT name FROM service_users WHERE id = ?", (su_id,)).fetchone()
    return row["name"] if row else None


def require_person(ctx: ToolContext, su_id: int) -> str:
    """The person's name, or an error if they don't exist or are outside the user's scope.

    Both cases give the same message, so the tool doesn't reveal who exists elsewhere.
    """
    in_scope, params = ctx.people_filter("id")
    row = ctx.conn.execute(
        f"SELECT name FROM service_users WHERE id = ? AND {in_scope}", [su_id, *params]
    ).fetchone()
    if row is None:
        raise ValueError(f"no service user with id {su_id}; use find_service_user first")
    return row["name"]


def page_info(total: int, returned: int) -> dict:
    """Standard totals block for any capped result, so a cut-off list is never mistaken for all of it."""
    return {"total": total, "returned": returned, "truncated": total > returned}
