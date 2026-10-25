"""Shared fixtures.

The suite runs on SYNTHETIC data built by app.synthetic: planted scenarios with
ground truth, so no external database or hand labels are needed and any dataset
size can be generated. The provided sample database is used only by
test_sample_dataset.py, which skips when SOURCE_DB_PATH isn't available.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402
from app.search.embeddings import HashEmbedder  # noqa: E402
from app.synthetic.builder import standard_scenarios  # noqa: E402
from app.synthetic.pipeline import build_world  # noqa: E402

needs_source = pytest.mark.skipif(
    config.SOURCE_DB_PATH is None or not Path(config.SOURCE_DB_PATH).exists(),
    reason=f"sample database not found at SOURCE_DB_PATH={config.SOURCE_DB_PATH}",
)


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """Standard scenarios (one of each pattern), oracle extraction, hash embeddings."""
    embedder = HashEmbedder()
    built = build_world(tmp_path_factory.mktemp("world"), standard_scenarios(), embedder=embedder)
    return {**built, "embedder": embedder}


@pytest.fixture(scope="session")
def truth(world):
    return world["truth"]


@pytest.fixture()
def ro(world):
    conn = db.connect_index_readonly(world["index"])
    yield conn
    conn.close()


def person(truth, scenario_kind: str) -> int:
    """service_user_id of the first person with this scenario."""
    return next(i for i, p in truth.people.items() if p["scenario"] == scenario_kind)
