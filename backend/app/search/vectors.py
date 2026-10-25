"""Vector similarity search over chunk_embeddings.

Two backends behind one function:
  * sqlite-vec (if the extension loads): a vec0 table partitioned by service user,
    with the org unit as a metadata column so team searches filter inside the index.
  * pure Python fallback: brute-force dot products over the filtered rows. macOS's
    built-in Python often can't load SQLite extensions, so this keeps things working.
    It refuses (rather than silently scanning a sample) when there are too many rows.

Both are exact (brute-force) search: fine per person or per team. Org-wide search over
millions of chunks is where production would move to pgvector with an HNSW index.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import List, Optional, Sequence, Tuple

from app import scope
from app.search.embeddings import from_blob, to_blob

log = logging.getLogger(__name__)

PYTHON_SCAN_LIMIT = 50_000  # most vectors the Python fallback will scan for one search
VEC_VERSION = "2"           # 2: org_unit_id metadata column


class TooManyVectors(RuntimeError):
    """The Python fallback would have to scan more than PYTHON_SCAN_LIMIT vectors."""


def load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:  # AttributeError on Pythons built without extension support
        log.info("sqlite-vec unavailable (%s): using the Python vector fallback", exc)
        return False


def vec_table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunk_vec'").fetchone() is not None


# Each embedding with what the vec0 table filters on. A visit belongs to its person's
# org unit and a form to its own, as in scope.chunks_filter.
_VEC_ROWS = """
    SELECT e.chunk_id, COALESCE(e.service_user_id, 0),
           COALESCE(CASE c.record_type WHEN 'visit' THEN su.org_unit_id ELSE f.org_unit_id END, 0),
           COALESCE(e.day, ''), e.vector
    FROM chunk_embeddings e
    JOIN chunks c ON c.id = e.chunk_id
    LEFT JOIN service_users su ON su.id = c.service_user_id
    LEFT JOIN forms f ON c.record_type = 'form' AND f.id = c.record_id
    WHERE e.dim = ?
"""


def sync_vec_table(conn: sqlite3.Connection, dim: int, chunk_ids: Optional[Sequence[int]] = None) -> None:
    """Bring the vec0 index up to date with chunk_embeddings. Requires sqlite-vec loaded.

    chunk_ids=None rebuilds the table (first build, new format or new model); otherwise
    only those chunks' rows are replaced.
    """
    if chunk_ids is None:
        conn.execute("DROP TABLE IF EXISTS chunk_vec")
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE chunk_vec USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                service_user_id INTEGER PARTITION KEY,
                org_unit_id INTEGER,
                day TEXT,
                embedding FLOAT[{dim}] distance_metric=cosine
            )
            """
        )
        conn.execute(f"INSERT INTO chunk_vec (chunk_id, service_user_id, org_unit_id, day, embedding) {_VEC_ROWS}",
                     (dim,))
        return
    ids = list(chunk_ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        conn.executemany("DELETE FROM chunk_vec WHERE chunk_id = ?", [(c,) for c in part])
        conn.execute(
            f"INSERT INTO chunk_vec (chunk_id, service_user_id, org_unit_id, day, embedding) "
            f"{_VEC_ROWS} AND e.chunk_id IN ({','.join('?' * len(part))})",
            (dim, *part),
        )


def search(
    conn: sqlite3.Connection,
    query_vec: List[float],
    k: int,
    service_user_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    use_sqlite_vec: bool = False,
    org_unit_ids: scope.OrgUnits = None,
) -> List[Tuple[int, float]]:
    """Return [(chunk_id, similarity)] best first, limited to org_unit_ids when given."""
    if org_unit_ids is not None and not org_unit_ids:
        return []
    if use_sqlite_vec:
        sql = "SELECT chunk_id, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ?"
        params: list = [to_blob(query_vec), k]
        if service_user_id is not None:
            sql += " AND service_user_id = ?"
            params.append(service_user_id)
        if org_unit_ids is not None:
            sql += f" AND org_unit_id IN ({','.join('?' * len(org_unit_ids))})"
            params += list(org_unit_ids)
        if date_from:
            sql += " AND day >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND day <= ?"
            params.append(date_to)
        return [(r[0], 1.0 - r[1]) for r in conn.execute(sql, params)]

    sql = "SELECT e.chunk_id, e.vector FROM chunk_embeddings e"
    params = []
    if org_unit_ids is not None:
        in_scope, params = scope.chunks_filter("c", org_unit_ids)
        sql += f" JOIN chunks c ON c.id = e.chunk_id AND {in_scope}"
    sql += " WHERE e.dim = ?"
    params.append(len(query_vec))
    if service_user_id is not None:
        sql += " AND e.service_user_id = ?"
        params.append(service_user_id)
    if date_from:
        sql += " AND e.day >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND e.day <= ?"
        params.append(date_to)
    sql += f" LIMIT {PYTHON_SCAN_LIMIT + 1}"
    scored = []
    for chunk_id, blob in conn.execute(sql, params):
        if len(scored) == PYTHON_SCAN_LIMIT:
            raise TooManyVectors(f"over {PYTHON_SCAN_LIMIT} vectors to scan without sqlite-vec; "
                                 "narrow the search to a person or dates")
        vec = from_blob(blob)
        scored.append((chunk_id, sum(a * b for a, b in zip(query_vec, vec))))  # vectors are normalised
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
