"""Helpers shared by the normalisation steps."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator


def iter_batches(
    src: sqlite3.Connection, table: str, columns: str, after_id: int, batch_size: int
) -> Iterator[list[sqlite3.Row]]:
    """Yield rows with id > after_id in id order, batch_size at a time."""
    last_id = after_id
    while True:
        rows = src.execute(
            f"SELECT {columns} FROM {table} WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            return
        yield rows
        last_id = rows[-1]["id"]


def parse_json_list(raw: str | None) -> list | None:
    """Parse a JSON array. Returns None if missing or malformed."""
    if raw is None or raw == "":
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def flag(idx: sqlite3.Connection, record_type: str, record_id: int, reason: str, detail: str) -> None:
    idx.execute(
        "INSERT OR REPLACE INTO review_queue (record_type, record_id, reason, detail) "
        "VALUES (?, ?, ?, ?)",
        (record_type, record_id, reason, detail),
    )


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = str(text).strip()
    return text or None
