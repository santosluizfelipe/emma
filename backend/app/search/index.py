"""Step 4: build the search index.

    python -m app.search.index

Chunking: one chunk per visit (note + activity comments, with who/when in the title)
and one per form section. Visits are short, so one visit = one chunk keeps each chunk
a complete, citable record. Form sections keep long care plans focused.

Incremental: visits and forms are paged from a watermark, so a run reads only records
added since the last one. Every chunk carries a content hash; only new or changed chunks
are (re-)embedded, and only their rows in the vector index are replaced.

Each keyword-index row also carries a person token and an org-unit token, so a search
for one person or one team reads only their postings instead of scoring everyone's.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import List, Optional, Tuple

from app import db
from app.search import vectors
from app.search.embeddings import Embedder, default_embedder, to_blob

log = logging.getLogger(__name__)

EMBED_BATCH = 100
FTS_VERSION = "2"  # 2: rows carry person / org-unit tokens


def person_token(service_user_id: Optional[int]) -> str:
    return f"zzsu{service_user_id or 0}"


def unit_token(org_unit_id: Optional[int]) -> str:
    return f"zzou{org_unit_id or 0}"


def fts_text(title: str, text: str, service_user_id: Optional[int], org_unit_id: Optional[int]) -> str:
    return f"{title}\n{text}\n{person_token(service_user_id)} {unit_token(org_unit_id)}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def upsert_chunk(idx: sqlite3.Connection, key: str, ref: str, record_type: str, record_id: int,
                 service_user_id: Optional[int], org_unit_id: Optional[int], day: Optional[str],
                 title: str, text: str) -> bool:
    """Insert or update a chunk and its FTS row. Returns True if it changed."""
    content_hash = _hash(title + "\n" + text)
    existing = idx.execute("SELECT id, content_hash FROM chunks WHERE key = ?", (key,)).fetchone()
    if existing and existing["content_hash"] == content_hash:
        return False
    if existing:
        chunk_id = existing["id"]
        idx.execute(
            "UPDATE chunks SET ref=?, record_type=?, record_id=?, service_user_id=?, day=?, title=?, "
            "text=?, content_hash=?, embedded_hash=NULL WHERE id=?",
            (ref, record_type, record_id, service_user_id, day, title, text, content_hash, chunk_id),
        )
        idx.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    else:
        chunk_id = idx.execute(
            "INSERT INTO chunks (key, ref, record_type, record_id, service_user_id, day, title, text, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, ref, record_type, record_id, service_user_id, day, title, text, content_hash),
        ).lastrowid
    idx.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                (chunk_id, fts_text(title, text, service_user_id, org_unit_id)))
    return True


def index_visits(idx: sqlite3.Connection, batch_size: int = 500) -> int:
    changed = 0
    last_id = int(db.get_meta(idx, "index:visits_through", "0"))
    while True:
        rows = idx.execute(
            """
            SELECT v.id, v.service_user_id, v.start_time, v.note, su.name AS su_name, su.org_unit_id,
                   c.name AS carer
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
                                        v["service_user_id"], v["org_unit_id"], db.day_of(v["start_time"]),
                                        title, "\n".join(lines))
            last_id = rows[-1]["id"]
            db.set_meta(idx, "index:visits_through", str(last_id))


def index_forms(idx: sqlite3.Connection, batch_size: int = 200) -> int:
    changed = 0
    last_id = int(db.get_meta(idx, "index:forms_through", "0"))
    while True:
        forms = idx.execute(
            """
            SELECT f.id, f.form_name, f.instance_title, f.created_at, f.org_unit_id, fs.service_user_id
            FROM forms f LEFT JOIN form_subjects fs ON fs.form_id = f.id
            WHERE f.id > ? ORDER BY f.id LIMIT ?
            """,
            (last_id, batch_size),
        ).fetchall()
        if not forms:
            return changed
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
                                            f["service_user_id"], f["org_unit_id"],
                                            (f["created_at"] or "")[:10] or None, title, "\n".join(lines))
            last_id = forms[-1]["id"]
            db.set_meta(idx, "index:forms_through", str(last_id))


def rebuild_fts_if_outdated(idx: sqlite3.Connection, batch_size: int = 2000) -> bool:
    """Re-write every keyword-index row in the current format (no API calls). Returns True if it ran."""
    if db.get_meta(idx, "fts:version") == FTS_VERSION:
        return False
    with idx:
        idx.execute("DELETE FROM chunks_fts")
        last_id = 0
        while True:
            rows = idx.execute(
                """
                SELECT c.id, c.title, c.text, c.service_user_id,
                       CASE c.record_type WHEN 'visit' THEN su.org_unit_id ELSE f.org_unit_id END AS org_unit_id
                FROM chunks c
                LEFT JOIN service_users su ON su.id = c.service_user_id
                LEFT JOIN forms f ON c.record_type = 'form' AND f.id = c.record_id
                WHERE c.id > ? ORDER BY c.id LIMIT ?
                """,
                (last_id, batch_size),
            ).fetchall()
            if not rows:
                break
            idx.executemany("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                            [(r["id"], fts_text(r["title"], r["text"], r["service_user_id"], r["org_unit_id"]))
                             for r in rows])
            last_id = rows[-1]["id"]
        db.set_meta(idx, "fts:version", FTS_VERSION)
    return True


def embed_pending(idx: sqlite3.Connection, embedder: Embedder) -> Tuple[List[int], bool]:
    """Embed chunks that are new or changed. Returns (chunk ids embedded, whether the model changed)."""
    model_changed = db.get_meta(idx, "embedding_model") not in (None, embedder.name)
    with idx:
        if model_changed:  # vectors from different models aren't comparable: redo them all
            idx.execute("UPDATE chunks SET embedded_hash = NULL")
        db.set_meta(idx, "embedding_model", embedder.name)
    embedded: List[int] = []
    while True:
        rows = idx.execute(
            """
            SELECT id, service_user_id, day, title, text, content_hash
            FROM chunks WHERE embedded_hash IS NULL ORDER BY id LIMIT ?
            """,
            (EMBED_BATCH,),
        ).fetchall()
        if not rows:
            return embedded, model_changed
        vecs = embedder.embed([r["title"] + "\n" + r["text"] for r in rows])
        with idx:
            for r, vec in zip(rows, vecs):
                idx.execute(
                    "INSERT OR REPLACE INTO chunk_embeddings VALUES (?, ?, ?, ?, ?, ?)",
                    (r["id"], r["service_user_id"], r["day"], embedder.name, len(vec), to_blob(vec)),
                )
                idx.execute("UPDATE chunks SET embedded_hash = ? WHERE id = ?", (r["content_hash"], r["id"]))
        embedded += [r["id"] for r in rows]
        log.info("embedded %d chunks", len(embedded))


def run(idx: sqlite3.Connection, embedder: Optional[Embedder] = None, use_default_embedder: bool = True) -> dict:
    fts_rebuilt = rebuild_fts_if_outdated(idx)  # first, so new rows aren't written twice
    changed = index_visits(idx) + index_forms(idx)
    if embedder is None and use_default_embedder:
        embedder = default_embedder()
    embedded: List[int] = []
    if embedder is None:
        log.warning("No embedding model configured: keyword search only (set OPENAI_API_KEY for vectors)")
    else:
        embedded, model_changed = embed_pending(idx, embedder)
        if vectors.load_sqlite_vec(idx):
            with idx:
                rebuild = (model_changed or db.get_meta(idx, "vec:version") != vectors.VEC_VERSION
                           or not vectors.vec_table_exists(idx))
                vectors.sync_vec_table(idx, embedder.dim, None if rebuild else embedded)
                db.set_meta(idx, "vec:version", vectors.VEC_VERSION)
            db.set_meta(idx, "vector_backend", "sqlite-vec")
        else:
            db.set_meta(idx, "vector_backend", "python")
        idx.commit()
    return {
        "chunks_changed": changed,
        "keyword_index_rebuilt": fts_rebuilt,
        "chunks_embedded": len(embedded),
        "embedding_model": db.get_meta(idx, "embedding_model"),
        "vector_backend": db.get_meta(idx, "vector_backend"),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    idx = db.connect_index()
    try:
        for key, value in run(idx).items():
            print(f"  {key:<22} {value}")
    finally:
        idx.close()


if __name__ == "__main__":
    main()
