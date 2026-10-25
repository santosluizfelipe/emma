"""Name -> service user linking (pure functions, no database)."""

from app.ingest.linking import Candidate, link_name, normalise_name

PEOPLE = [Candidate(8, "Raymond Clarke", 1), Candidate(9, "Iris Bennett", 1), Candidate(10, "Cynthia Ofori", 1)]


def test_normalise_name():
    assert normalise_name("  Raymond  CLARKE. ") == "raymond clarke"
    assert normalise_name("Zoë O’Neil") == "zoe o neil"


def test_exact_match_ignores_case_and_spacing():
    assert link_name("raymond   clarke", PEOPLE).service_user_id == 8


def test_small_typo_links_with_fuzzy_confidence():
    result = link_name("Raymond Clark", PEOPLE)
    assert (result.service_user_id, result.method) == (8, "fuzzy") and result.confidence < 1.0


def test_unknown_name_is_not_guessed():
    assert link_name("John Smith", PEOPLE).method == "unmatched"


def test_duplicate_names_are_ambiguous():
    result = link_name("Iris Bennett", PEOPLE + [Candidate(11, "Iris Bennett", 1)])
    assert (result.service_user_id, result.method) == (None, "ambiguous")
