"""The write path is incremental: a run does work in proportion to what changed, and
building in several runs gives exactly what one full build gives."""

import shutil
import sqlite3

from app import config, db
from app.ingest import extract, normalise, rollup, rules
from app.search import hybrid, index
from app.search.embeddings import HashEmbedder
from app.synthetic.builder import build_dataset, demo_population
from app.synthetic.oracle import OracleExtractor


def write_path(source, index_db, extractor, embedder) -> dict:
    out = {"normalise": normalise.run(source_path=source, index_path=index_db),
           "extract": extract.run(extractor, index_path=index_db)}
    conn = db.connect_index(index_db)
    try:
        out["rules"] = rules.run(conn)
        out["rollup"] = rollup.run(conn)
        out["index"] = index.run(conn, embedder=embedder, use_default_embedder=False)
    finally:
        conn.close()
    return out


def snapshot(index_db) -> dict:
    conn = db.connect_index_readonly(index_db)
    try:
        return {
            "risk": conn.execute("SELECT * FROM su_daily_risk ORDER BY service_user_id, day").fetchall(),
            "issues": conn.execute("SELECT * FROM record_issues ORDER BY 1, 2, 3, 4, 5, 6").fetchall(),
            "signals": conn.execute("SELECT visit_id, category, severity, evidence FROM visit_signals "
                                    "ORDER BY visit_id, category").fetchall(),
            "chunks": conn.execute("SELECT key, content_hash, embedded_hash FROM chunks ORDER BY key").fetchall(),
            "searches": [sorted(r["ref"] for r in hybrid.search(conn, "breakfast unsteady", service_user_id=su, k=20))
                         for su in (1, 2, 3)],
        }
    finally:
        conn.close()


def test_building_in_two_runs_matches_one_full_build(tmp_path, monkeypatch):
    full_src, part_src = tmp_path / "full.db", tmp_path / "part.db"
    truth = build_dataset(full_src, demo_population(40, seed=5), days=14, seed=5)
    oracle, embedder = OracleExtractor(truth), HashEmbedder()

    # Run 1: only the older visits (lower ids) exist, and "today" is four days earlier
    shutil.copy(full_src, part_src)
    with sqlite3.connect(part_src) as src:
        cut = src.execute("SELECT MAX(id) * 6 / 10 FROM visits").fetchone()[0]
        src.execute("DELETE FROM visits WHERE id > ?", (cut,))
    monkeypatch.setattr(config, "AS_OF_OVERRIDE", "2025-11-12 23:59:59")
    write_path(part_src, tmp_path / "incremental.db", oracle, embedder)

    # Run 2: the rest arrives and "today" moves on
    monkeypatch.setattr(config, "AS_OF_OVERRIDE", None)
    second = write_path(full_src, tmp_path / "incremental.db", oracle, embedder)
    assert second["rollup"]["mode"] == "incremental"

    write_path(full_src, tmp_path / "full_build.db", oracle, embedder)
    assert snapshot(tmp_path / "incremental.db") == snapshot(tmp_path / "full_build.db")


def test_a_run_with_no_new_data_does_no_work(tmp_path):
    source = tmp_path / "source.db"
    truth = build_dataset(source, demo_population(30, seed=2), days=14, seed=2)
    oracle, embedder = OracleExtractor(truth), HashEmbedder()
    write_path(source, tmp_path / "index.db", oracle, embedder)
    again = write_path(source, tmp_path / "index.db", oracle, embedder)
    assert again["normalise"]["new_visits"] == 0
    assert again["extract"]["visits_processed"] == 0
    assert again["rules"] == {"visits_checked": 0, "people_checked": 0}
    assert again["rollup"]["rows_written"] == 0
    assert again["index"]["chunks_changed"] == 0 and again["index"]["chunks_embedded"] == 0


class FailsOnce(OracleExtractor):
    """The oracle, but every third visit fails on its first attempt."""
    name = "fails-once"

    def __init__(self, truth):
        super().__init__(truth)
        self.seen: set = set()

    def extract(self, visit):
        if visit.visit_id % 3 == 0 and visit.visit_id not in self.seen:
            self.seen.add(visit.visit_id)
            raise RuntimeError("temporary API error")
        return super().extract(visit)


def test_failed_visits_are_retried_on_the_next_run(tmp_path):
    source, index_db = tmp_path / "source.db", tmp_path / "index.db"
    truth = build_dataset(source, demo_population(10, seed=4), days=7, seed=4)
    normalise.run(source_path=source, index_path=index_db)
    extractor = FailsOnce(truth)
    first = extract.run(extractor, index_path=index_db)
    assert first["failures"] > 0
    second = extract.run(extractor, index_path=index_db)
    assert second["visits_processed"] == first["failures"] and second["failures"] == 0
    assert extract.run(extractor, index_path=index_db)["visits_processed"] == 0
