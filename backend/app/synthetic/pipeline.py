"""Build a synthetic dataset AND its derived store in one call (tests, evals, demos)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from app import db
from app.ingest import extract, normalise, rollup, rules
from app.search import index
from app.search.embeddings import Embedder
from app.synthetic.builder import Truth, build_dataset
from app.synthetic.oracle import OracleExtractor
from app.synthetic.scenarios import Scenario


def build_world(folder, scenarios: List[Scenario], extractor=None, embedder: Optional[Embedder] = None,
                days: int = 14, seed: int = 7) -> dict:
    """Write source.db (+ truth) and index.db into `folder` and run the full write path.

    extractor defaults to the oracle (ground truth); pass a real extractor to test it.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source, index_db = folder / "source.db", folder / "index.db"
    for p in folder.glob("index.db*"):
        p.unlink()
    truth: Truth = build_dataset(source, scenarios, days=days, seed=seed)
    normalise.run(full=True, source_path=source, index_path=index_db)
    extract.run(extractor or OracleExtractor(truth), index_path=index_db)
    conn = db.connect_index(index_db)
    try:
        rules.run(conn)
        rollup.run(conn)
        index.run(conn, embedder=embedder, use_default_embedder=False)
    finally:
        conn.close()
    return {"source": source, "index": index_db, "truth": truth}
