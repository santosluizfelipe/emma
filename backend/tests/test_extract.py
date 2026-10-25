"""Step 2: extraction pipeline mechanics (quality is measured in eval/cases)."""

from types import SimpleNamespace

from app import db
from app.ingest import extract
from app.ingest.extractors import Extractor, KeywordExtractor, LLMExtractor, VisitInput, redact
from app.ingest.signals import ExtractedSignal, ExtractionResult
from app.synthetic.pipeline import build_world
from app.synthetic.builder import standard_scenarios


def test_stored_signals_match_what_the_extractor_returned(ro, truth):
    stored = {(r["visit_id"], r["category"], r["severity"]) for r in ro.execute("SELECT * FROM visit_signals")}
    assert stored == {(s.visit_id, s.category, s.severity) for s in truth.signals}


def test_code_computes_contradictions_and_care_plan_matches(ro, truth):
    contradicting = [r[0] for r in ro.execute("SELECT visit_id FROM visit_signals WHERE contradicts_status = 1")]
    assert contradicting == truth.contradictions
    # every synthetic care plan lists the person's focus areas as known risks
    assert ro.execute("SELECT MIN(matches_care_plan_risk) FROM visit_signals").fetchone()[0] == 1


def test_rerun_is_cached(world):
    from app.synthetic.oracle import OracleExtractor
    assert extract.run(OracleExtractor(world["truth"]), index_path=world["index"])["visits_processed"] == 0


def test_keyword_baseline_recovers_every_planted_signal(tmp_path):
    w = build_world(tmp_path, standard_scenarios(), extractor=KeywordExtractor())
    conn = db.connect_index_readonly(w["index"])
    got = {(r[0], r[1]) for r in conn.execute("SELECT visit_id, category FROM visit_signals")}
    assert got == {(s.visit_id, s.category) for s in w["truth"].signals}  # traps produce nothing


class Hallucinating(Extractor):
    name = "hallucinating"

    def extract(self, visit):
        return [ExtractedSignal(category="mood", severity="medium", source="note", escalated=False,
                                evidence="They seemed very sad and tearful.")]


def test_invented_quotes_are_flagged_and_not_scored(tmp_path):
    w = build_world(tmp_path, standard_scenarios()[:1], extractor=Hallucinating())
    conn = db.connect_index_readonly(w["index"])
    assert conn.execute("SELECT MAX(evidence_verified) FROM visit_signals").fetchone()[0] == 0
    assert conn.execute("SELECT MAX(rolling_7d) FROM su_daily_risk").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM record_issues WHERE issue = 'unverified_evidence'").fetchone()[0] > 0


# ------------------------------------------------------------- LLM extractor with a fake client

def _fake_client(result: ExtractionResult):
    message = SimpleNamespace(parsed=result, refusal=None)
    parse = lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=message)])  # noqa: E731
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
    assert "Alfred" not in text and "Nkemelu" not in text and "[SU]'s tea" in text
    messages = LLMExtractor(client=_fake_client(ExtractionResult(signals=[])), model="x").build_messages(VISIT)
    assert "Alfred" not in messages[1]["content"]


def test_llm_evidence_is_unredacted_and_verified():
    fake = ExtractionResult(signals=[
        ExtractedSignal(category="falls_mobility", severity="high", source="note", escalated=True,
                        evidence="[SU] had a slight near-fall; the office was informed."),
        ExtractedSignal(category="mood", severity="medium", source="note", escalated=False,
                        evidence="Alfred seemed very sad."),
    ])
    signals = LLMExtractor(client=_fake_client(fake), model="x").extract(VISIT)
    assert signals[0].evidence.startswith("Alfred had a slight near-fall")
    assert extract.evidence_is_verbatim(signals[0].evidence, VISIT)
    assert not extract.evidence_is_verbatim(signals[1].evidence, VISIT)
