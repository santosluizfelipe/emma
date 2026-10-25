"""Step 4: search index."""

from app.search import hybrid, vectors
from app.search.embeddings import HashEmbedder
from tests.conftest import person


def test_one_chunk_per_visit_and_per_form_section(ro):
    assert ro.execute("SELECT COUNT(*) FROM chunks WHERE record_type = 'visit'").fetchone()[0] == \
        ro.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    assert ro.execute("SELECT COUNT(DISTINCT ref) FROM chunks WHERE record_type = 'form'").fetchone()[0] == \
        ro.execute("SELECT COUNT(*) FROM forms").fetchone()[0]


def test_keyword_search_uses_stemming(ro, truth):
    bruising_visit = next(s.visit_id for s in truth.signals if s.category == "skin_injury")
    assert f"V{bruising_visit}" in [r["ref"] for r in hybrid.search(ro, "bruise", k=10)]


def test_filters_by_person_and_date(ro, truth):
    su = person(truth, "gradual_decline")
    results = hybrid.search(ro, "unsteady confused", service_user_id=su, date_from="2025-11-12", k=10)
    assert results and all(r["service_user_id"] == su and r["day"] >= "2025-11-12" for r in results)


def test_hybrid_reports_both_retrievers(ro, world, truth):
    s = next(s for s in truth.signals if s.category == "safeguarding")
    results = {r["ref"]: r for r in hybrid.search(ro, s.evidence, embedder=world["embedder"], k=5)}
    assert set(results[f"V{s.visit_id}"]["found_by"]) == {"keyword", "vector"}


def test_python_vector_fallback_matches_sqlite_vec(ro, world, truth):
    query = world["embedder"].embed(["cough sputum discoloured"])[0]
    su = next(s.service_user_id for s in truth.signals if s.category == "respiratory")
    python_hits = [c for c, _ in vectors.search(ro, query, 5, service_user_id=su, use_sqlite_vec=False)]
    assert python_hits
    if vectors.load_sqlite_vec(ro):
        vec_hits = [c for c, _ in vectors.search(ro, query, 5, service_user_id=su, use_sqlite_vec=True)]
        assert python_hits[:3] == vec_hits[:3]


def test_wrong_embedding_model_is_ignored(ro):
    assert hybrid.vector_search(ro, "fall", HashEmbedder(dim=64)) == []
