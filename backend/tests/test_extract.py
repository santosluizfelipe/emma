"""Step 2: signal extraction."""

from types import SimpleNamespace

from app.ingest import extract
from app.ingest.extractors import LLMExtractor, VisitInput, redact
from app.ingest.signals import ExtractedSignal, ExtractionResult
from tests.conftest import needs_source

pytestmark = needs_source

# Hand-checked: the 17 visits in the sample with something a manager should know
EXPECTED_VISITS = {190, 193, 196, 199, 202, 205, 208, 232, 235, 238, 241, 244, 247, 250, 259, 293, 327}


def test_keyword_baseline_finds_every_risk_visit_and_no_routine_ones(ro):
    visits = {r[0] for r in ro.execute("SELECT DISTINCT visit_id FROM visit_signals")}
    assert visits == EXPECTED_VISITS


def test_negation_is_respected(ro):
    # V205 says "no injury was noted" - that must not become a skin/injury signal
    cats = {r[0] for r in ro.execute("SELECT category FROM visit_signals WHERE visit_id = 205")}
    assert cats == {"falls_mobility"}


def test_key_signals(ro):
    def sig(visit, category):
        return ro.execute(
            "SELECT * FROM visit_signals WHERE visit_id = ? AND category = ?", (visit, category)
        ).fetchone()

    near_fall = sig(205, "falls_mobility")
    assert near_fall["severity"] == "high" and near_fall["escalated"] == 1
    assert near_fall["matches_care_plan_risk"] == 1  # Alfred's plan lists falls

    meds = sig(293, "medication")
    assert meds["severity"] == "high" and meds["contradicts_status"] == 1

    assert sig(327, "safeguarding")["severity"] == "high"
    assert sig(250, "respiratory")["severity"] == "high"


def test_all_evidence_is_verbatim(ro):
    assert ro.execute("SELECT COUNT(*) FROM visit_signals WHERE evidence_verified = 0").fetchone()[0] == 0


def test_rerun_is_cached(pipeline):
    from app.ingest.extractors import KeywordExtractor
    summary = extract.run(KeywordExtractor(), index_path=pipeline["path"])
    assert summary["visits_processed"] == 0


# ------------------------------------------------------------- LLM extractor with a fake client

def _fake_client(result: ExtractionResult):
    message = SimpleNamespace(parsed=result, refusal=None)
    parse = lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=message)], kwargs=kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))


VISIT = VisitInput(
    visit_id=1, service_user_id=6, first_name="Alfred", full_name="Alfred Nkemelu",
    start_time="2025-11-15 08:00:00",
    note="Alfred had a slight near-fall; the office was informed. Alfred Nkemelu's tea was cold.",
    activities=[{"name": "Medication", "status": "Completed", "comment": "Given."}],
    known_risks="Known risks include falls.",
)


def test_names_are_redacted_before_reaching_the_model():
    text = redact(VISIT.note, ["Alfred Nkemelu", "Alfred", "Nkemelu"])
    assert "Alfred" not in text and "Nkemelu" not in text
    assert "[SU]'s tea" in text
    messages = LLMExtractor(client=_fake_client(ExtractionResult(signals=[])), model="x").build_messages(VISIT)
    assert "Alfred" not in messages[1]["content"]


def test_llm_evidence_is_unredacted_and_verified():
    fake = ExtractionResult(signals=[
        ExtractedSignal(category="falls_mobility", severity="high", source="note", escalated=True,
                        evidence="[SU] had a slight near-fall; the office was informed."),
        ExtractedSignal(category="mood", severity="medium", source="note", escalated=False,
                        evidence="Alfred seemed very sad."),  # hallucinated: not in the note
    ])
    signals = LLMExtractor(client=_fake_client(fake), model="x").extract(VISIT)
    assert signals[0].evidence.startswith("Alfred had a slight near-fall")
    assert extract.evidence_is_verbatim(signals[0].evidence, VISIT)
    assert not extract.evidence_is_verbatim(signals[1].evidence, VISIT)
