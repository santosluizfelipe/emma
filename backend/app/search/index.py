"""Step 4: build the search index.

    python -m app.search.index

Chunking: one chunk per visit (note + activity comments, with who/when in the title)
and one per form section. Visits are short, so one visit = one chunk keeps each chunk
a complete, citable record. Form sections keep long care plans focused.

Incremental: every chunk carries a content hash. Unchanged chunks are skipped; only
new or changed ones are re-indexed and re-embedded (in batches).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Optional

from app import db
from app.search import vectors
from app.search.embeddings import Embedder, default_embedder, to_blob

log = logging.getLogger(__name__)

EMBED_BATCH = 100


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def upsert_chunk(idx: sqlite3.Connection, key: str, ref: str, record_type: str, record_id: int,
                 service_user_id: Optional[int], day: Optional[str], title: str, text: str) -> bool:
    """Insert or update a chunk and its FTS row. Returns True if it changed."""
    content_hash = _hash(title + "\n" + text)
    existing = idx.execute("SELECT id, content_hash FROM chunks WHERE key = ?", (key,)).fetchone()
    if existing and existing["content_hash"] == content_hash:
        return False
    if existing:
        chunk_id = existing["id"]
        idx.execute(
            "UPDATE chunks SET ref=?, record_type=?, record_id=?, service_user_id=?, day=?, title=?, "
            "text=?, content_hash=? WHERE id=?",
            (ref, record_type, record_id, service_user_id, day, title, text, content_hash, chunk_id),
        )
        idx.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    else:
        chunk_id = idx.execute(
            "INSERT INTO chunks (key, ref, record_type, record_id, service_user_id, day, title, text, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, ref, record_type, record_id, service_user_id, day, title, text, content_hash),
        ).lastrowid
    idx.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, title + "\n" + text))
    return True


def index_visits(idx: sqlite3.Connection, batch_size: int = 500) -> int:
    changed = 0
    last_id = 0
    while True:
        rows = idx.execute(
            """
            SELECT v.id, v.service_user_id, v.start_time, v.note, su.name AS su_name, c.name AS carer
            FROM visits v
            JOIN service_users su ON su.id = v.service_user_id
            LEFT JOIN carers c ON c.id = v.carer_id
            WHERE v.id > ? ORDER BY v.id LIMIT ?
            """,
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            return changed
        with idx:
            for v in rows:
                acts = idx.execute(
                    "SELECT name, status, comment FROM visit_activities WHERE visit_id = ? ORDER BY position",
                    (v["id"],),
                ).fetchall()
                lines = [v["note"] or ""] + [
                    f"{a['name']} ({a['status']}): {a['comment'] or 'no comment'}" for a in acts
                ]
                title = f"Visit V{v['id']} | {v['su_name']} | {v['start_time'][:16]} | carer {v['carer'] or 'unknown'}"
                changed += upsert_chunk(idx, f"V{v['id']}", f"V{v['id']}", "visit", v["id"],
                                        v["service_user_id"], db.day_of(v["start_time"]),
                                        title, "\n".join(lines))
        last_id = rows[-1]["id"]


def index_forms(idx: sqlite3.Connection) -> int:
    changed = 0
    forms = idx.execute(
        """
        SELECT f.id, f.form_name, f.instance_title, f.created_at, fs.service_user_id
        FROM forms f LEFT JOIN form_subjects fs ON fs.form_id = f.id
        ORDER BY f.id
        """
    ).fetchall()
    with idx:
        for f in forms:
            sections: dict = {}
            for a in idx.execute(
                "SELECT section, question, answer FROM form_answers WHERE form_id = ? ORDER BY position",
                (f["id"],),
            ):
                sections.setdefault(a["section"] or "", []).append(f"{a['question']}: {a['answer']}")
            for i, (section, lines) in enumerate(sections.items()):
                title = f"Form F{f['id']} | {f['instance_title']} | {section} | {f['created_at']}"
                changed += upsert_chunk(idx, f"F{f['id']}#{i}", f"F{f['id']}", "form", f["id"],
                                        f["service_user_id"], (f["created_at"] or "")[:10] or None,
                                        title, "\n".join(lines))
    return changed


def embed_pending(idx: sqlite3.Connection, embedder: Embedder) -> int:
    embedded = 0
    while True:
        rows = idx.execute(
            """
            SELECT c.id, c.service_user_id, c.day, c.title, c.text, c.content_hash
            FROM chunks c LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id
            WHERE c.embedded_hash IS NULL OR c.embedded_hash <> c.content_hash
               OR e.model IS NULL OR e.model <> ?
            LIMIT ?
            """,
            (embedder.name, EMBED_BATCH),
        ).fetchall()
        if not rows:
            return embedded
        vecs = embedder.embed([r["title"] + "\n" + r["text"] for r in rows])
        with idx:
            for r, vec in zip(rows, vecs):
                idx.execute(
                    "INSERT OR REPLACE INTO chunk_embeddings VALUES (?, ?, ?, ?, ?, ?)",
                    (r["id"], r["service_user_id"], r["day"], embedder.name, len(vec), to_blob(vec)),
                )
                idx.execute("UPDATE chunks SET embedded_hash = ? WHERE id = ?", (r["content_hash"], r["id"]))
        embedded += len(rows)
        log.info("embedded %d chunks", embedded)


def run(idx: sqlite3.Connection, embedder: Optional[Embedder] = None, use_default_embedder: bool = True) -> dict:
    changed = index_visits(idx) + index_forms(idx)
    if embedder is None and use_default_embedder:
        embedder = default_embedder()
    embedded = 0
    if embedder is None:
        log.warning("No embedding model configured: keyword search only (set OPENAI_API_KEY for vectors)")
    else:
        embedded = embed_pending(idx, embedder)
        db.set_meta(idx, "embedding_model", embedder.name)
        if vectors.load_sqlite_vec(idx):
            with idx:
                vectors.sync_vec_table(idx, embedder.dim)
            db.set_meta(idx, "vector_backend", "sqlite-vec")
        else:
            db.set_meta(idx, "vector_backend", "python")
        idx.commit()
    return {
        "chunks": idx.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "chunks_changed": changed,
        "chunks_embedded": embedded,
        "embedding_model": db.get_meta(idx, "embedding_model"),
        "vector_backend": db.get_meta(idx, "vector_backend"),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    idx = db.connect_index()
    try:
        for key, value in run(idx).items():
            print(f"  {key:<16} {value}")
    finally:
        idx.close()


if __name__ == "__main__":
    main()
