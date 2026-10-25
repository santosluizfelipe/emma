"""Hybrid search: keyword (BM25) + vector similarity, fused with Reciprocal Rank Fusion.

Why both:
  * Keywords are precise for specific wording ("sputum", "SG-2025-001", a carer's name).
  * Vectors catch different wording for the same meaning ("had a tumble" ~ "fall").
RRF combines the two ranked lists without having to calibrate their scores:
    score(chunk) = sum over lists of 1 / (60 + rank)

Note: search is for finding records. "Who is at risk?" is answered from the
extracted signals instead, because similarity search can't count, can't order
events in time, and is fooled by negation ("no concerns noted").
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import List, Optional

from app import db
from app.search import vectors
from app.search.embeddings import Embedder

log = logging.getLogger(__name__)

RRF_K = 60
CANDIDATES = 30  # per retriever, before fusion
STOPWORDS = set(
    "a an the and or of to in on for with was were is are be been has have had did does do "
    "any anyone anything about who what when which this that these those there their his her "
    "him she he it its at by from as me my we our you your show tell find all".split()
)


def fts_query(text: str) -> Optional[str]:
    tokens = [t for t in re.findall(r"[A-Za-z0-9-]+", text.lower()) if t not in STOPWORDS and len(t) > 1]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens[:12])


def _filters(alias: str, service_user_id, date_from, date_to, record_type) -> tuple:
    clauses, params = [], []
    if service_user_id is not None:
        clauses.append(f"{alias}.service_user_id = ?")
        params.append(service_user_id)
    if date_from:
        clauses.append(f"{alias}.day >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{alias}.day <= ?")
        params.append(date_to)
    if record_type:
        clauses.append(f"{alias}.record_type = ?")
        params.append(record_type)
    return clauses, params


def keyword_search(conn, query, service_user_id=None, date_from=None, date_to=None,
                   record_type=None, limit=CANDIDATES) -> List[int]:
    match = fts_query(query)
    if not match:
        return []
    clauses, params = _filters("c", service_user_id, date_from, date_to, record_type)
    where = " AND ".join(["chunks_fts MATCH ?"] + clauses)
    rows = conn.execute(
        f"""
        SELECT c.id FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE {where}
        ORDER BY bm25(chunks_fts)
        LIMIT ?
        """,
        [match] + params + [limit],
    ).fetchall()
    return [r[0] for r in rows]


def vector_search(conn, query, embedder: Embedder, service_user_id=None, date_from=None,
                  date_to=None, record_type=None, limit=CANDIDATES) -> List[int]:
    if db.get_meta(conn, "embedding_model") != embedder.name:
        return []  # index was built with a different model: vectors not comparable
    query_vec = embedder.embed([query])[0]
    use_vec = db.get_meta(conn, "vector_backend") == "sqlite-vec" and vectors.load_sqlite_vec(conn)
    hits = vectors.search(conn, query_vec, limit * 2, service_user_id, date_from, date_to, use_vec)
    ids = [chunk_id for chunk_id, _ in hits]
    if record_type and ids:
        keep = {r[0] for r in conn.execute(
            f"SELECT id FROM chunks WHERE record_type = ? AND id IN ({','.join('?' * len(ids))})",
            [record_type] + ids,
        )}
        ids = [i for i in ids if i in keep]
    return ids[:limit]


def search(conn: sqlite3.Connection, query: str, *, embedder: Optional[Embedder] = None,
           service_user_id: Optional[int] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, record_type: Optional[str] = None, k: int = 8) -> List[dict]:
    """Return the top-k chunks with which retriever(s) found them."""
    filters = dict(service_user_id=service_user_id, date_from=date_from, date_to=date_to, record_type=record_type)
    ranked = {"keyword": keyword_search(conn, query, **filters)}
    if embedder is not None:
        try:
            ranked["vector"] = vector_search(conn, query, embedder, **filters)
        except Exception as exc:  # never fail a search because the embedding API hiccuped
            log.warning("vector search skipped: %s", exc)

    scores: dict = {}
    found_by: dict = {}
    for name, ids in ranked.items():
        for rank, chunk_id in enumerate(ids):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            found_by.setdefault(chunk_id, []).append(name)

    top = sorted(scores, key=scores.get, reverse=True)[:k]
    if not top:
        return []
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT id, ref, record_type, service_user_id, day, title, text FROM chunks "
        f"WHERE id IN ({','.join('?' * len(top))})",
        top,
    )}
    return [
        {
            "ref": rows[i]["ref"],
            "title": rows[i]["title"],
            "day": rows[i]["day"],
            "service_user_id": rows[i]["service_user_id"],
            "text": rows[i]["text"],
            "found_by": found_by[i],
            "score": round(scores[i], 4),
        }
        for i in top if i in rows
    ]
