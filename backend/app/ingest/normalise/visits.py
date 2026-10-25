"""Visits: copy each visit and flatten its activities JSON into visit_activities."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from app import db
from app.ingest.normalise.common import clean, flag, iter_batches, parse_json_list

log = logging.getLogger(__name__)


def ingest_visits(src: sqlite3.Connection, idx: sqlite3.Connection, batch_size: int) -> int:
    watermark = int(db.get_meta(idx, "watermark:visits", "0"))
    count = 0
    columns = "id, service_user_id, carer_id, start_time, end_time, status, activities, note"

    for batch in iter_batches(src, "visits", columns, watermark, batch_size):
        with idx:  # one transaction per batch
            for v in batch:
                content_hash = hashlib.sha256(
                    f'{v["note"] or ""}\x1f{v["activities"] or ""}'.encode()
                ).hexdigest()[:16]
                idx.execute(
                    "INSERT OR REPLACE INTO visits "
                    "(id, service_user_id, carer_id, start_time, end_time, status, note, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (v["id"], v["service_user_id"], v["carer_id"], v["start_time"],
                     v["end_time"], v["status"], clean(v["note"]), content_hash),
                )
                idx.execute("DELETE FROM visit_activities WHERE visit_id = ?", (v["id"],))

                activities = parse_json_list(v["activities"])
                if activities is None:
                    flag(idx, "visit", v["id"], "malformed_activities_json",
                         "activities column could not be parsed")
                    continue

                for pos, act in enumerate(activities):
                    if not isinstance(act, dict):
                        continue
                    idx.execute(
                        "INSERT INTO visit_activities "
                        "(visit_id, position, name, status, description, comment, "
                        " is_medication, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (v["id"], pos, clean(act.get("name")), clean(act.get("status")),
                         clean(act.get("description")), clean(act.get("comment")),
                         1 if act.get("is_medication") else 0, clean(act.get("closed_date"))),
                    )
            db.set_meta(idx, "watermark:visits", str(batch[-1]["id"]))
        count += len(batch)
        log.info("visits: %d processed (up to id %d)", count, batch[-1]["id"])
    return count
