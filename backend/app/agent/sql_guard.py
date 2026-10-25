"""Guardrails for the agent's free-form SQL tool.

The model writes SQL; the code decides what's allowed:
  * exactly one statement, and it must be a SELECT (CTEs and UNIONs of SELECTs ok)
  * only allow-listed tables (no sqlite_master, no FTS/vector internals)
  * a LIMIT is always applied (capped at SQL_MAX_ROWS)
  * executed on a read-only connection with a time limit
Safety never depends on the model behaving.
"""

from __future__ import annotations

import sqlite3
import time
from typing import List, Optional

import sqlglot
from sqlglot import exp

from app import config

ALLOWED_TABLES = {
    "service_users", "carers", "visits", "visit_activities", "forms", "form_answers",
    "form_subjects", "visit_signals", "su_daily_risk", "record_issues",
}


FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in
    ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Attach", "Detach")
    if hasattr(exp, name)
)


class SQLRejected(ValueError):
    pass


def validate(sql: str, max_rows: int = config.SQL_MAX_ROWS) -> str:
    """Return a safe, LIMITed version of the query or raise SQLRejected."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SQLRejected(f"could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise SQLRejected("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise SQLRejected("only SELECT queries are allowed")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRejected(f"{type(node).__name__} is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if table.db or name not in ALLOWED_TABLES:
            raise SQLRejected(
                f"table '{table.sql()}' is not available. Allowed: {', '.join(sorted(ALLOWED_TABLES))}"
            )

    limit = tree.args.get("limit")
    current: Optional[int] = None
    if limit is not None:
        try:
            current = int(limit.expression.name)
        except (AttributeError, ValueError):
            current = None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows)
    return tree.sql(dialect="sqlite")


def execute(conn: sqlite3.Connection, sql: str) -> dict:
    safe_sql = validate(sql)
    deadline = time.monotonic() + config.SQL_TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cursor = conn.execute(safe_sql)
        columns: List[str] = [d[0] for d in cursor.description or []]
        rows = [list(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise SQLRejected(f"query took longer than {config.SQL_TIMEOUT_SECONDS}s") from exc
        raise SQLRejected(f"SQL error: {exc}") from exc
    finally:
        conn.set_progress_handler(None, 0)
    return {"sql": safe_sql, "columns": columns, "rows": rows, "row_count": len(rows),
            "truncated": len(rows) >= config.SQL_MAX_ROWS}
