"""Database access.

Two databases, two roles:
  * source  -> the care provider's care_data.db, opened READ-ONLY
  * index   -> our derived store (agent_index.db); the agent only ever reads this
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Bump when schema.sql changes. The derived store is disposable, so an outdated
# one is simply dropped and rebuilt on the next ingestion run.
SCHEMA_VERSION = 2


def connect_source(path: Path | None = None) -> sqlite3.Connection:
    """Open the source database read-only. Any write raises an error."""
    path = path or config.SOURCE_DB_PATH
    if path is None:
        raise RuntimeError(
            "SOURCE_DB_PATH is not set. Add it to backend/.env, e.g. "
            "SOURCE_DB_PATH=/Users/you/Downloads/care_data.db"
        )
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Source database not found at SOURCE_DB_PATH={path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except (sqlite3.DatabaseError, PermissionError) as exc:
        raise PermissionError(
            f"Cannot open {path} ({exc}). On macOS, folders like Downloads, Desktop and "
            "Documents are privacy-protected: allow your terminal app under System Settings > "
            "Privacy & Security > Files and Folders, or move the database to another folder "
            "and update SOURCE_DB_PATH."
        ) from exc
    conn.row_factory = sqlite3.Row
    return conn


def connect_index(path: Path | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the derived store."""
    path = Path(path or config.INDEX_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the ingest writer
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        _drop_all(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def connect_index_readonly(path: Path | None = None) -> sqlite3.Connection:
    """Read-only view of the derived store, used by the agent's tools."""
    path = Path(path or config.INDEX_DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m app.ingest` first")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _drop_all(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%' AND name NOT LIKE '%vec_%'"
    ).fetchall()
    for row in rows:
        try:
            conn.execute(f'DROP {row["type"].upper()} IF EXISTS "{row["name"]}"')
        except sqlite3.OperationalError:
            pass  # e.g. a vector table whose extension isn't loaded; harmless
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def day_of(timestamp: str) -> str:
    """'2025-11-15 08:00:00' -> '2025-11-15'"""
    return timestamp[:10]


def get_as_of(conn: sqlite3.Connection) -> str:
    """The system's notion of "now".

    Defaults to the latest visit in the data (recorded during ingestion), so
    questions like "this week" work on a historical sample. Set AS_OF to pin it.
    """
    if config.AS_OF_OVERRIDE:
        return config.AS_OF_OVERRIDE
    value = get_meta(conn, "as_of")
    if value is None:
        raise RuntimeError("as_of not set: run ingestion first (python -m app.ingest)")
    return value
