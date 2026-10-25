"""Reference data: service users and carers (small; fully upserted each run)."""

from __future__ import annotations

import sqlite3


def sync_reference_tables(src: sqlite3.Connection, idx: sqlite3.Connection) -> None:
    """Service users and carers: small tables, fully upserted each run."""
    for row in src.execute(
        "SELECT id, name, date_of_birth, sex, status, org_unit_id FROM service_users"
    ):
        idx.execute(
            "INSERT OR REPLACE INTO service_users VALUES (?, ?, ?, ?, ?, ?)", tuple(row)
        )
    for row in src.execute("SELECT id, name, status, org_unit_id FROM carers"):
        idx.execute("INSERT OR REPLACE INTO carers VALUES (?, ?, ?, ?)", tuple(row))
