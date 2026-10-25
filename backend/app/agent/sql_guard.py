"""Guardrails for the agent's free-form SQL tool.

The model writes SQL; the code decides what's allowed:
  * exactly one statement, and it must be a SELECT (CTEs and UNIONs of SELECTs ok)
  * only allow-listed tables (no sqlite_master, no FTS/vector internals); a CTE can't
    borrow a table's name to get round this
  * when the request is limited to org units, every table is replaced by the rows in scope
  * a LIMIT is always applied (capped at SQL_MAX_ROWS)
  * executed on a read-only connection with a time limit
Safety never depends on the model behaving.
"""

from __future__ import annotations

import sqlite3
import time
from typing import List, Optional, Set

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app import config, scope

ALLOWED_TABLES = {
    "service_users", "carers", "visits", "visit_activities", "forms", "form_answers",
    "form_subjects", "visit_signals", "su_daily_risk", "record_issues",
}

# Each table as seen by a user limited to org units. {units} is a list of integer ids
# built in code, never text from the model.
_PEOPLE = "SELECT id FROM service_users WHERE org_unit_id IN ({units})"
_FORMS = "SELECT id FROM forms WHERE org_unit_id IN ({units})"
SCOPED_TABLES = {
    "service_users": "SELECT * FROM service_users WHERE org_unit_id IN ({units})",
    "carers": "SELECT * FROM carers WHERE org_unit_id IN ({units})",
    "visits": f"SELECT * FROM visits WHERE service_user_id IN ({_PEOPLE})",
    "visit_activities": "SELECT * FROM visit_activities WHERE visit_id IN "
                        f"(SELECT id FROM visits WHERE service_user_id IN ({_PEOPLE}))",
    "visit_signals": f"SELECT * FROM visit_signals WHERE service_user_id IN ({_PEOPLE})",
    "su_daily_risk": f"SELECT * FROM su_daily_risk WHERE service_user_id IN ({_PEOPLE})",
    "record_issues": f"SELECT * FROM record_issues WHERE service_user_id IN ({_PEOPLE})",
    "forms": "SELECT * FROM forms WHERE org_unit_id IN ({units})",
    "form_answers": f"SELECT * FROM form_answers WHERE form_id IN ({_FORMS})",
    "form_subjects": f"SELECT * FROM form_subjects WHERE form_id IN ({_FORMS})",
}
assert set(SCOPED_TABLES) == ALLOWED_TABLES


FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in
    ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Attach", "Detach")
    if hasattr(exp, name)
)


class SQLRejected(ValueError):
    pass


def _cte_references(tree: exp.Expression) -> Set[int]:
    """ids of the Table nodes that refer to a CTE rather than a real table.

    Uses sqlglot's scope analysis, so in "WITH meta AS (SELECT * FROM meta) ..." the
    inner `meta` is the real table. Anything not recognised as a CTE reference is
    treated as a real table (fail closed).
    """
    refs: Set[int] = set()
    try:
        for sc in traverse_scope(tree):
            for node, source in sc.selected_sources.values():
                if isinstance(source, Scope) and isinstance(node, exp.Table):
                    refs.add(id(node))
    except Exception as exc:  # sqlglot raises various errors on unusual queries
        raise SQLRejected(f"could not analyse SQL: {exc}") from exc
    return refs


def validate(sql: str, max_rows: int = config.SQL_MAX_ROWS, org_unit_ids: scope.OrgUnits = None) -> str:
    """Return a safe, LIMITed (and, with org_unit_ids, scoped) version of the query or raise SQLRejected."""
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

    # A CTE named like a table would also capture the table names inside the scope filters below
    for cte in tree.find_all(exp.CTE):
        if cte.alias_or_name.lower() in ALLOWED_TABLES:
            raise SQLRejected(f"a CTE can't be named like a table ('{cte.alias_or_name}'); rename it")
    cte_refs = _cte_references(tree)
    real_tables = [t for t in tree.find_all(exp.Table) if id(t) not in cte_refs]
    for table in real_tables:
        if table.db or table.name.lower() not in ALLOWED_TABLES:
            raise SQLRejected(
                f"table '{table.sql()}' is not available. Allowed: {', '.join(sorted(ALLOWED_TABLES))}"
            )

    if org_unit_ids is not None:
        units = ",".join(str(int(u)) for u in org_unit_ids) or "NULL"
        for table in real_tables:
            scoped = sqlglot.parse_one(SCOPED_TABLES[table.name.lower()].format(units=units), read="sqlite")
            table.replace(scoped.subquery(alias=table.alias_or_name))

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


def execute(conn: sqlite3.Connection, sql: str, org_unit_ids: scope.OrgUnits = None) -> dict:
    safe_sql = validate(sql, org_unit_ids=org_unit_ids)
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
