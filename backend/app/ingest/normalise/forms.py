"""Forms: copy headers, flatten form_data JSON into form_answers, and work out
who each form is about (form_subjects).

Safeguarding forms have no service_user_id in the source; the person is only named
inside the JSON, so the name is resolved with app.ingest.linking.
"""

from __future__ import annotations

import logging
import sqlite3

from app import db
from app.ingest.linking import Candidate, link_name
from app.ingest.normalise.common import clean, flag, parse_json_list

log = logging.getLogger(__name__)

# Questions in a form that name the person the form is about
SUBJECT_QUESTIONS = ("service user involved", "service user", "full name")


def find_subject_name(answers: list[tuple[str | None, str | None, str | None]]) -> str | None:
    """Return the answer to the first question that names the form's subject."""
    for wanted in SUBJECT_QUESTIONS:
        for _section, question, answer in answers:
            if question and answer and question.strip().lower() == wanted:
                return answer
    return None


def load_candidates(idx: sqlite3.Connection, org_unit_id: int | None) -> list[Candidate]:
    """Service users the form could be about: same org unit only (tenant safety)."""
    rows = idx.execute(
        "SELECT id, name, org_unit_id FROM service_users WHERE org_unit_id IS ?",
        (org_unit_id,),
    ).fetchall()
    return [Candidate(r["id"], r["name"], r["org_unit_id"]) for r in rows]


def resolve_subject(
    idx: sqlite3.Connection, form: sqlite3.Row, answers: list[tuple]
) -> int | None:
    """Decide which service user a form is about, record how we know, and return their id."""
    form_id = form["id"]
    written_name = find_subject_name(answers)

    if form["service_user_id"] is not None:
        idx.execute(
            "INSERT OR REPLACE INTO form_subjects VALUES (?, ?, 1.0, 'direct', ?)",
            (form_id, form["service_user_id"], written_name),
        )
        return form["service_user_id"]

    if not written_name:
        # e.g. an organisation-level form that is not about one person
        idx.execute(
            "INSERT OR REPLACE INTO form_subjects VALUES (?, NULL, 0.0, 'not_applicable', NULL)",
            (form_id,),
        )
        return None

    result = link_name(written_name, load_candidates(idx, form["org_unit_id"]))
    idx.execute(
        "INSERT OR REPLACE INTO form_subjects VALUES (?, ?, ?, ?, ?)",
        (form_id, result.service_user_id, result.confidence, result.method, written_name),
    )
    if result.service_user_id is None or result.method == "fuzzy":
        flag(idx, "form", form_id, f"subject_{result.method}",
             f"'{written_name}': {result.detail}")
    return result.service_user_id


def ingest_forms(src: sqlite3.Connection, idx: sqlite3.Connection, batch_size: int) -> int:
    watermark = int(db.get_meta(idx, "watermark:forms", "0"))
    count = 0
    columns = (
        "f.id, f.service_user_id, f.org_unit_id, f.form_config_id, f.entity_type, "
        "f.instance_title, f.form_data, f.created_at, "
        "fc.name AS form_name, ft.name AS form_type"
    )
    table = (
        "forms f LEFT JOIN form_configs fc ON fc.id = f.form_config_id "
        "LEFT JOIN form_types ft ON ft.id = fc.form_type_id"
    )

    last_id = watermark
    while True:
        batch = src.execute(
            f"SELECT {columns} FROM {table} WHERE f.id > ? ORDER BY f.id LIMIT ?",
            (last_id, batch_size),
        ).fetchall()
        if not batch:
            break

        with idx:
            for f in batch:
                idx.execute(
                    "INSERT OR REPLACE INTO forms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (f["id"], f["form_config_id"], f["form_name"], f["form_type"],
                     f["entity_type"], f["instance_title"], f["org_unit_id"], f["created_at"]),
                )
                idx.execute("DELETE FROM form_answers WHERE form_id = ?", (f["id"],))

                sections = parse_json_list(f["form_data"])
                if sections is None:
                    flag(idx, "form", f["id"], "malformed_form_json",
                         "form_data column could not be parsed")
                    sections = []

                answers: list[tuple] = []
                for section in sections:
                    if not isinstance(section, dict):
                        continue
                    for item in section.get("items") or []:
                        if isinstance(item, dict):
                            answers.append((clean(section.get("section")),
                                            clean(item.get("question")),
                                            clean(item.get("answer"))))

                idx.executemany(
                    "INSERT INTO form_answers (form_id, position, section, question, answer) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(f["id"], pos, *a) for pos, a in enumerate(answers)],
                )
                # a new care plan or assessment can settle an "assessment not reviewed" issue
                db.enqueue(idx, "queue_rules_people", [resolve_subject(idx, f, answers)])

            last_id = batch[-1]["id"]
            db.set_meta(idx, "watermark:forms", str(last_id))
        count += len(batch)
        log.info("forms: %d processed (up to id %d)", count, last_id)
    return count
