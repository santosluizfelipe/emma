"""Smoke test on the provided sample database (skipped when SOURCE_DB_PATH is missing).

Everything else in the suite runs on synthetic data. This checks the facts the
README quotes about the real sample.
"""

import pytest

from app import db
from app.ingest import extract, normalise, rollup, rules
from app.ingest.extractors import KeywordExtractor
from tests.conftest import needs_source

pytestmark = needs_source


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("sample") / "index.db"
    normalise.run(full=True, index_path=path)
    extract.run(KeywordExtractor(), index_path=path)
    conn = db.connect_index(path)
    rules.run(conn)
    rollup.run(conn)
    yield conn
    conn.close()


def test_sample_loads(sample):
    assert sample.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 336
    assert sample.execute("SELECT COUNT(*) FROM visit_activities").fetchone()[0] == 1148


def test_sample_safeguarding_links(sample):
    rows = sample.execute(
        "SELECT su.name FROM form_subjects fs JOIN service_users su ON su.id = fs.service_user_id "
        "WHERE fs.form_id IN (21, 22, 23) ORDER BY fs.form_id").fetchall()
    assert [r[0] for r in rows] == ["Raymond Clarke", "Iris Bennett", "Cynthia Ofori"]


def test_sample_trend_alerts_fire_on_13_november(sample):
    for su in (6, 7):  # Alfred Nkemelu, Doris Whitfield
        assert sample.execute("SELECT MIN(day) FROM su_daily_risk WHERE service_user_id = ? AND alert <> 'none'",
                              (su,)).fetchone()[0] == "2025-11-13"
