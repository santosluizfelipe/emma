"""Step 1: copy and flatten the source data into the derived store.

    python -m app.ingest.normalise          # incremental: only new records
    python -m app.ingest.normalise --full   # rebuild from scratch

(`python -m app.ingest` runs this and every later step.)

Scale properties:
  * Streams the source in fixed-size batches (keyset pagination), so memory use
    is flat however large the tables are.
  * Incremental: a per-table watermark means each run only touches new rows.
    (The source has no updated_at column, so edits to existing rows are not
    detected; production would use change-data-capture or an updated_at column.)
  * Idempotent: re-running a batch replaces its rows rather than duplicating them.

Modules:
  common.py     shared helpers (batching, JSON parsing, review flags)
  reference.py  service users, carers
  visits.py     visits + visit_activities
  forms.py      forms, form_answers, form_subjects
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app import config, db
from app.ingest.normalise.forms import ingest_forms
from app.ingest.normalise.reference import sync_reference_tables
from app.ingest.normalise.visits import ingest_visits

DERIVED_TABLES = (
    "visits", "visit_activities", "forms", "form_answers",
    "form_subjects", "review_queue", "service_users", "carers", "meta",
)


def reset(idx: sqlite3.Connection) -> None:
    with idx:
        for table in DERIVED_TABLES:
            idx.execute(f"DELETE FROM {table}")


def run(full: bool = False, source_path=None, index_path=None) -> dict:
    src = db.connect_source(source_path)
    idx = db.connect_index(index_path)
    try:
        if full:
            reset(idx)
        with idx:
            sync_reference_tables(src, idx)

        batch_size = config.INGEST_BATCH_SIZE
        new_visits = ingest_visits(src, idx, batch_size)
        new_forms = ingest_forms(src, idx, batch_size)

        with idx:
            # "Now" is the latest visit that has already started: rotas also hold future visits
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            latest = (idx.execute("SELECT MAX(start_time) AS t FROM visits WHERE start_time <= ?", (now,)).fetchone()["t"]
                      or idx.execute("SELECT MAX(start_time) AS t FROM visits").fetchone()["t"])
            if latest:
                db.set_meta(idx, "as_of", latest)

        summary = {
            "new_visits": new_visits,
            "new_forms": new_forms,
            "visits": idx.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
            "activities": idx.execute("SELECT COUNT(*) FROM visit_activities").fetchone()[0],
            "forms": idx.execute("SELECT COUNT(*) FROM forms").fetchone()[0],
            "form_answers": idx.execute("SELECT COUNT(*) FROM form_answers").fetchone()[0],
            "linked_forms": idx.execute(
                "SELECT COUNT(*) FROM form_subjects WHERE service_user_id IS NOT NULL"
            ).fetchone()[0],
            "review_queue": idx.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0],
            "as_of": db.get_meta(idx, "as_of"),
        }
        return summary
    finally:
        src.close()
        idx.close()
