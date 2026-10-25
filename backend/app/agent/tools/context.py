"""What every tool receives, plus small shared helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from app import db
from app.search.embeddings import Embedder

OPT_DATE = {"type": "string", "description": "YYYY-MM-DD"}


@dataclass
class ToolContext:
    conn: sqlite3.Connection        # read-only connection to the derived store
    as_of: str                      # "now" for this dataset
    embedder: Optional[Embedder] = None

    @property
    def today(self) -> str:
        return db.day_of(self.as_of)

    def days_ago(self, days: int) -> str:
        return (date.fromisoformat(self.today) - timedelta(days=days - 1)).isoformat()


def person_name(ctx: ToolContext, su_id: Optional[int]) -> Optional[str]:
    if su_id is None:
        return None
    row = ctx.conn.execute("SELECT name FROM service_users WHERE id = ?", (su_id,)).fetchone()
    return row["name"] if row else None


def require_person(ctx: ToolContext, su_id: int) -> str:
    name = person_name(ctx, su_id)
    if name is None:
        raise ValueError(f"no service user with id {su_id}; use find_service_user first")
    return name
