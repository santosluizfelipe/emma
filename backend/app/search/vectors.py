"""Vector similarity search over chunk_embeddings.

Two backends behind one function:
  * sqlite-vec (if the extension loads): a vec0 table partitioned by service user.
  * pure Python fallback: brute-force dot products over the filtered rows. macOS's
    built-in Python often can't load SQLite extensions, so this keeps things working.

Both are exact (brute-force) search. That is fine here because queries are almost
always filtered to one service user first (hundreds of vectors). Org-wide search over
millions of chunks is where production would move to pgvector with an HNSW index.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import List, Optional, Tuple

from app.search.embeddings import from_blob, to_blob

log = logging.getLogger(__name__)

PYTHON_SCAN_LIMIT = 50_000  # guardrail for the fallback's unfiltered searches


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


def sync_vec_table(conn: sqlite3.Connection, dim: int) -> None:
    """(Re)build the vec0 index from chunk_embeddings. Requires sqlite-vec loaded.

    Rebuilt in full for simplicity (fine at this size); production would upsert only
    the changed rows, or use pgvector where the index is maintained by the database.
    """
    conn.execute("DROP TABLE IF EXISTS chunk_vec")
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE chunk_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            service_user_id INTEGER PARTITION KEY,
            day TEXT,
            embedding FLOAT[{dim}] distance_metric=cosine
        )
        """
    )
    conn.execute(
        "INSERT INTO chunk_vec (chunk_id, service_user_id, day, embedding) "
        "SELECT chunk_id, COALESCE(service_user_id, 0), COALESCE(day, ''), vector "
        "FROM chunk_embeddings WHERE dim = ?",
        (dim,),
    )


def search(
    conn: sqlite3.Connection,
    query_vec: List[float],
    k: int,
    service_user_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    use_sqlite_vec: bool = False,
) -> List[Tuple[int, float]]:
    """Return [(chunk_id, similarity)] best first."""
    if use_sqlite_vec:
        sql = "SELECT chunk_id, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ?"
        params: list = [to_blob(query_vec), k]
        if service_user_id is not None:
            sql += " AND service_user_id = ?"
            params.append(service_user_id)
        if date_from:
            sql += " AND day >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND day <= ?"
            params.append(date_to)
        return [(r[0], 1.0 - r[1]) for r in conn.execute(sql, params)]

    sql = "SELECT chunk_id, vector FROM chunk_embeddings WHERE dim = ?"
    params = [len(query_vec)]
    if service_user_id is not None:
        sql += " AND service_user_id = ?"
        params.append(service_user_id)
    if date_from:
        sql += " AND day >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND day <= ?"
        params.append(date_to)
    sql += f" LIMIT {PYTHON_SCAN_LIMIT}"
    scored = []
    for chunk_id, blob in conn.execute(sql, params):
        vec = from_blob(blob)
        scored.append((chunk_id, sum(a * b for a, b in zip(query_vec, vec))))  # vectors are normalised
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
