import sys
from pathlib import Path

import pytest

# Allow `import app` when running pytest from the backend/ folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402

needs_source = pytest.mark.skipif(
    config.SOURCE_DB_PATH is None or not Path(config.SOURCE_DB_PATH).exists(),
    reason=f"source database not found at SOURCE_DB_PATH={config.SOURCE_DB_PATH}; fix backend/.env",
)


@pytest.fixture(scope="session")
def pipeline(tmp_path_factory):
    """Run the whole write path once, offline: keyword extractor + hash embeddings."""
    if config.SOURCE_DB_PATH is None or not Path(config.SOURCE_DB_PATH).exists():
        pytest.skip("SOURCE_DB_PATH not set")
    from app.ingest import extract, normalise, rollup, rules
    from app.ingest.extractors import KeywordExtractor
    from app.search import index
    from app.search.embeddings import HashEmbedder

    path = tmp_path_factory.mktemp("index") / "agent_index.db"
    normalise.run(full=True, index_path=path)
    extract.run(KeywordExtractor(), index_path=path)
    embedder = HashEmbedder()
    conn = db.connect_index(path)
    rules.run(conn)
    rollup.run(conn)
    index.run(conn, embedder=embedder)
    conn.close()
    return {"path": path, "embedder": embedder}


@pytest.fixture()
def ro(pipeline):
    conn = db.connect_index_readonly(pipeline["path"])
    yield conn
    conn.close()
