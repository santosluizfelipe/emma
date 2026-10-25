"""Step 4: search index."""

from app.search import hybrid, vectors
from app.search.embeddings import HashEmbedder
from tests.conftest import needs_source

pytestmark = needs_source


def test_one_chunk_per_visit_plus_form_sections(ro):
    visits = ro.execute("SELECT COUNT(*) FROM chunks WHERE record_type = 'visit'").fetchone()[0]
    forms = ro.execute("SELECT COUNT(DISTINCT ref) FROM chunks WHERE record_type = 'form'").fetchone()[0]
    assert (visits, forms) == (336, 23)


def test_keyword_search_uses_stemming(ro):
    refs = [r["ref"] for r in hybrid.search(ro, "bruise", k=5)]
    assert "V259" in refs  # note says "Bruising"


def test_filters_by_person_and_date(ro):
    results = hybrid.search(ro, "breathless", service_user_id=7, date_from="2025-11-15", k=10)
    assert results and all(r["service_user_id"] == 7 and r["day"] >= "2025-11-15" for r in results)


def test_hybrid_reports_both_retrievers(ro, pipeline):
    results = hybrid.search(ro, "slight near-fall, steadied straight away", embedder=pipeline["embedder"], k=3)
    by_ref = {r["ref"]: r for r in results}
    assert "V205" in by_ref
    assert set(by_ref["V205"]["found_by"]) == {"keyword", "vector"}


def test_python_vector_fallback_matches_sqlite_vec(ro, pipeline):
    embedder: HashEmbedder = pipeline["embedder"]
    query = embedder.embed(["cough sputum discoloured"])[0]
    python_hits = [c for c, _ in vectors.search(ro, query, 5, service_user_id=7, use_sqlite_vec=False)]
    if vectors.load_sqlite_vec(ro):
        vec_hits = [c for c, _ in vectors.search(ro, query, 5, service_user_id=7, use_sqlite_vec=True)]
        assert python_hits[:3] == vec_hits[:3]
    assert python_hits


def test_wrong_embedding_model_is_ignored(ro):
    other = HashEmbedder(dim=64)  # different model name than the index was built with
    assert hybrid.vector_search(ro, "fall", other) == []
