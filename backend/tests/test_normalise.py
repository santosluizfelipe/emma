"""Step 1 checks: the derived store matches the source, and the source is untouched."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from app import config, db
from app.ingest import normalise
from app.ingest.linking import Candidate, link_name, normalise_name

SOURCE = config.SOURCE_DB_PATH

pytestmark = pytest.mark.skipif(
    SOURCE is None or not SOURCE.exists(),
    reason=f"source database not found at SOURCE_DB_PATH={SOURCE}; fix backend/.env",
)


def file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def index_path(tmp_path):
    return tmp_path / "agent_index.db"


@pytest.fixture()
def built(index_path):
    before = file_hash(SOURCE)
    summary = normalise.run(full=True, index_path=index_path)
    conn = db.connect_index(index_path)
    yield summary, conn, before
    conn.close()


def test_counts_match_source(built):
    summary, conn, _ = built
    assert summary["visits"] == 336
    assert summary["activities"] == 1148
    assert summary["forms"] == 23
    assert summary["as_of"] == "2025-11-16 17:30:00"


def test_every_form_answer_is_flattened(built):
    _, conn, _ = built
    src = db.connect_source()
    expected = 0
    for (raw,) in src.execute("SELECT form_data FROM forms"):
        expected += sum(len(s["items"]) for s in json.loads(raw))
    assert conn.execute("SELECT COUNT(*) FROM form_answers").fetchone()[0] == expected


def test_safeguarding_forms_are_linked_to_the_right_people(built):
    _, conn, _ = built
    rows = conn.execute(
        """
        SELECT f.instance_title, s.name, fs.method
        FROM form_subjects fs
        JOIN forms f ON f.id = fs.form_id
        JOIN service_users s ON s.id = fs.service_user_id
        WHERE f.form_type = 'Safeguarding/Complaints'
        ORDER BY f.id
        """
    ).fetchall()
    assert [(r["name"], r["method"]) for r in rows] == [
        ("Raymond Clarke", "exact"),
        ("Iris Bennett", "exact"),
        ("Cynthia Ofori", "exact"),
    ]


def test_every_form_has_a_subject_and_nothing_needs_review(built):
    _, conn, _ = built
    assert conn.execute(
        "SELECT COUNT(*) FROM form_subjects WHERE service_user_id IS NOT NULL"
    ).fetchone()[0] == 23
    assert conn.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


def test_contradiction_is_preserved_for_later_rules(built):
    """Visit 293: Medication marked Completed, comment says it was not given."""
    _, conn, _ = built
    row = conn.execute(
        "SELECT status, comment FROM visit_activities WHERE visit_id = 293 AND name = 'Medication'"
    ).fetchone()
    assert row["status"] == "Completed"
    assert "not to have given" in row["comment"]


def test_null_comments_are_kept_as_null(built):
    _, conn, _ = built
    assert conn.execute(
        "SELECT COUNT(*) FROM visit_activities WHERE comment IS NULL"
    ).fetchone()[0] == 13


def test_rerun_is_incremental_and_idempotent(built, index_path):
    summary_again = normalise.run(full=False, index_path=index_path)
    assert summary_again["new_visits"] == 0
    assert summary_again["new_forms"] == 0
    assert summary_again["activities"] == 1148


def test_source_is_read_only_and_unchanged(built):
    _, _, before = built
    src = db.connect_source()
    with pytest.raises(sqlite3.OperationalError):
        src.execute("UPDATE visits SET note = 'x' WHERE id = 1")
    assert file_hash(SOURCE) == before


# ---------------------------------------------------------------- linking unit tests

PEOPLE = [
    Candidate(8, "Raymond Clarke", 1),
    Candidate(9, "Iris Bennett", 1),
    Candidate(10, "Cynthia Ofori", 1),
]


def test_normalise_name():
    assert normalise_name("  Raymond  CLARKE. ") == "raymond clarke"
    assert normalise_name("Zoë O’Neil") == "zoe o neil"


def test_exact_match_ignores_case_and_spacing():
    assert link_name("raymond   clarke", PEOPLE).service_user_id == 8


def test_small_typo_links_with_fuzzy_confidence():
    result = link_name("Raymond Clark", PEOPLE)
    assert result.service_user_id == 8
    assert result.method == "fuzzy"
    assert result.confidence < 1.0


def test_unknown_name_is_not_guessed():
    result = link_name("John Smith", PEOPLE)
    assert result.service_user_id is None
    assert result.method == "unmatched"


def test_duplicate_names_are_ambiguous():
    people = PEOPLE + [Candidate(11, "Iris Bennett", 1)]
    result = link_name("Iris Bennett", people)
    assert result.service_user_id is None
    assert result.method == "ambiguous"
