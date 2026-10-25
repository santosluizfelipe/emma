"""Step 1: the derived store mirrors the source; the source is never modified."""

import hashlib
import json
import sqlite3

import pytest

from app import db
from app.ingest import normalise


def _source_counts(path):
    conn = sqlite3.connect(path)
    visits = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    activities = sum(len(json.loads(a)) for (a,) in conn.execute("SELECT activities FROM visits"))
    answers = sum(sum(len(s["items"]) for s in json.loads(d)) for (d,) in conn.execute("SELECT form_data FROM forms"))
    forms = conn.execute("SELECT COUNT(*) FROM forms").fetchone()[0]
    as_of = conn.execute("SELECT MAX(start_time) FROM visits").fetchone()[0]
    conn.close()
    return visits, activities, forms, answers, as_of


def test_counts_match_source(world, ro):
    visits, activities, forms, answers, as_of = _source_counts(world["source"])
    assert ro.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == visits
    assert ro.execute("SELECT COUNT(*) FROM visit_activities").fetchone()[0] == activities
    assert ro.execute("SELECT COUNT(*) FROM forms").fetchone()[0] == forms
    assert ro.execute("SELECT COUNT(*) FROM form_answers").fetchone()[0] == answers
    assert db.get_as_of(ro) == as_of


def test_safeguarding_forms_link_to_the_right_people(ro, truth):
    links = dict(ro.execute(
        "SELECT form_id, service_user_id FROM form_subjects WHERE method IN ('exact', 'fuzzy')").fetchall())
    assert links == truth.safeguarding_links
    assert ro.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 0


def test_null_comments_are_kept(ro, truth):
    assert ro.execute("SELECT COUNT(*) FROM visit_activities WHERE comment IS NULL").fetchone()[0] == \
        len(truth.completed_without_comment)


def test_rerun_is_incremental(world, tmp_path):
    index = tmp_path / "i.db"
    first = normalise.run(full=True, source_path=world["source"], index_path=index)
    again = normalise.run(source_path=world["source"], index_path=index)
    assert first["new_visits"] > 0 and again["new_visits"] == 0 and again["visits"] == first["visits"]


def test_source_is_read_only_and_unchanged(world):
    before = hashlib.sha256(world["source"].read_bytes()).hexdigest()
    src = db.connect_source(world["source"])
    with pytest.raises(sqlite3.OperationalError):
        src.execute("UPDATE visits SET note = 'x' WHERE id = 1")
    src.close()
    assert hashlib.sha256(world["source"].read_bytes()).hexdigest() == before


def test_missing_source_gives_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="SOURCE_DB_PATH"):
        db.connect_source(tmp_path / "nope.db")
