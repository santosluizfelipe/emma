"""Step 2: read each visit once and store what it means as structured signals.

    python -m app.ingest.extract                      # LLM if OPENAI_API_KEY is set, else keyword
    python -m app.ingest.extract --extractor keyword  # offline baseline

Scale properties:
  * Each visit is extracted once. extraction_runs remembers (visit, content hash,
    extractor), so re-runs only touch new or changed visits and a crash is safe to resume.
  * Visits are pulled in batches and extracted concurrently; the DB writer stays single.
  * The LLM never sees names (replaced with [SU]) and only gets one visit + that
    person's care-plan risks, so each call is small and fixed-size.
  * Deterministic post-processing doesn't trust the model blindly: every evidence
    quote is checked against the source text, and care-plan matching and status
    contradictions are computed in code.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from app import config, db
from app.ingest.extractors import Extractor, KeywordExtractor, LLMExtractor, VisitInput
from app.ingest.signals import ExtractedSignal

log = logging.getLogger(__name__)

BATCH_SIZE = 50

# Words in a care plan's "Known risks" that correspond to each category
CARE_PLAN_TERMS = {
    "falls_mobility": r"fall|mobil|balance|unsteady",
    "cognition": r"disorient|confus|dementia|memory|wander",
    "intake": r"intake|nutrition|hydrat|appetite|fluid|eat|drink",
    "respiratory": r"breath|chest|cough|copd|respirat|inhaler",
    "skin_injury": r"skin|pressure|bruis|wound",
    "medication": r"medication|side effect",
    "mood": r"mood|anxi|withdrawn|low|distress",
    "pain": r"pain",
    "continence": r"continen",
    "safeguarding": r"safeguard|vulnerab|financial|abuse",
}


def make_extractor(kind: str = "auto") -> Extractor:
    if kind == "keyword" or (kind == "auto" and not config.OPENAI_API_KEY):
        if kind == "auto":
            log.warning("OPENAI_API_KEY not set: using the offline keyword extractor")
        return KeywordExtractor()
    return LLMExtractor()


# --------------------------------------------------------------------------- inputs

def care_plan_context(idx: sqlite3.Connection, service_user_id: int) -> Tuple[str, str]:
    """(conditions, known risks) from the person's most recent care plan."""
    rows = idx.execute(
        """
        SELECT fa.question, fa.answer
        FROM form_answers fa
        JOIN form_subjects fs ON fs.form_id = fa.form_id
        JOIN forms f ON f.id = fa.form_id
        WHERE fs.service_user_id = ? AND f.form_name = 'Care Plan'
          AND fa.question IN ('Health conditions and diagnoses', 'Known risks')
        ORDER BY f.created_at DESC
        LIMIT 2
        """,
        (service_user_id,),
    ).fetchall()
    answers = {r["question"]: r["answer"] or "" for r in rows}
    return answers.get("Health conditions and diagnoses", ""), answers.get("Known risks", "")


def pending_visits(idx: sqlite3.Connection, extractor_name: str, limit: int) -> List[sqlite3.Row]:
    """Visits never extracted, changed since, or extracted by a different extractor."""
    return idx.execute(
        """
        SELECT v.id, v.service_user_id, v.start_time, v.note, v.content_hash,
               su.name AS full_name
        FROM visits v
        JOIN service_users su ON su.id = v.service_user_id
        LEFT JOIN extraction_runs r ON r.visit_id = v.id
        WHERE r.visit_id IS NULL
           OR r.content_hash <> v.content_hash
           OR r.extractor <> ?
           OR r.error IS NOT NULL
        ORDER BY v.id
        LIMIT ?
        """,
        (extractor_name, limit),
    ).fetchall()


def build_input(idx: sqlite3.Connection, row: sqlite3.Row, plans: Dict[int, Tuple[str, str]]) -> VisitInput:
    su = row["service_user_id"]
    if su not in plans:
        plans[su] = care_plan_context(idx, su)
    conditions, risks = plans[su]
    activities = [
        dict(a) for a in idx.execute(
            "SELECT name, status, comment FROM visit_activities WHERE visit_id = ? ORDER BY position",
            (row["id"],),
        )
    ]
    return VisitInput(
        visit_id=row["id"],
        service_user_id=su,
        first_name=row["full_name"].split()[0],
        full_name=row["full_name"],
        start_time=row["start_time"],
        note=row["note"] or "",
        activities=activities,
        known_risks=risks,
        conditions=conditions,
    )


# --------------------------------------------------------------------------- post-processing

def _squash(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".")


def evidence_is_verbatim(evidence: str, visit: VisitInput) -> bool:
    haystack = _squash(" ".join([visit.note] + [a.get("comment") or "" for a in visit.activities]))
    needle = _squash(evidence)
    return bool(needle) and needle in haystack


def matches_care_plan(category: str, known_risks: str) -> bool:
    terms = CARE_PLAN_TERMS.get(category)
    return bool(terms and known_risks and re.search(terms, known_risks, re.I))


def contradicts_status(signal: ExtractedSignal, visit: VisitInput) -> bool:
    """A medication problem on a visit whose Medication task is marked Completed."""
    if signal.category != "medication":
        return False
    return any(
        (a.get("name") or "").lower() == "medication" and (a.get("status") or "").lower() == "completed"
        for a in visit.activities
    )


def dedupe(signals: List[ExtractedSignal]) -> List[ExtractedSignal]:
    """Keep the most severe signal per category for a visit."""
    rank = config.SEVERITY_WEIGHTS
    best: Dict[str, ExtractedSignal] = {}
    for s in signals:
        if s.category not in best or rank[s.severity] > rank[best[s.category].severity]:
            best[s.category] = s
    return list(best.values())


def save(idx: sqlite3.Connection, visit: VisitInput, row: sqlite3.Row, extractor: str,
         signals: Optional[List[ExtractedSignal]], error: Optional[str]) -> int:
    idx.execute("DELETE FROM visit_signals WHERE visit_id = ?", (visit.visit_id,))
    count = 0
    for s in dedupe(signals or []):
        verified = evidence_is_verbatim(s.evidence, visit)
        idx.execute(
            """
            INSERT INTO visit_signals (visit_id, service_user_id, observed_at, day, category,
                severity, severity_score, evidence, source, escalated, matches_care_plan_risk,
                contradicts_status, evidence_verified, extractor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (visit.visit_id, visit.service_user_id, visit.start_time, db.day_of(visit.start_time),
             s.category, s.severity, config.SEVERITY_WEIGHTS[s.severity], s.evidence.strip(),
             s.source, int(s.escalated), int(matches_care_plan(s.category, visit.known_risks)),
             int(contradicts_status(s, visit)), int(verified), extractor),
        )
        count += 1
    idx.execute(
        "INSERT OR REPLACE INTO extraction_runs (visit_id, content_hash, extractor, signal_count, error) "
        "VALUES (?, ?, ?, ?, ?)",
        (visit.visit_id, row["content_hash"], extractor, count, error),
    )
    return count


# --------------------------------------------------------------------------- entry point

def run(extractor: Optional[Extractor] = None, index_path=None, max_visits: Optional[int] = None) -> dict:
    extractor = extractor or make_extractor("auto")
    idx = db.connect_index(index_path)
    processed = signals_total = failures = 0
    plans: Dict[int, Tuple[str, str]] = {}

    def work(visit: VisitInput):
        try:
            return extractor.extract(visit), None
        except Exception as exc:  # keep going; failed visits are retried next run
            log.warning("visit %s failed: %s", visit.visit_id, exc)
            return None, f"{type(exc).__name__}: {exc}"[:500]

    try:
        workers = 1 if isinstance(extractor, KeywordExtractor) else config.EXTRACTION_CONCURRENCY
        attempted: set = set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while max_visits is None or processed < max_visits:
                rows = [r for r in pending_visits(idx, extractor.name, BATCH_SIZE) if r["id"] not in attempted]
                if not rows:
                    break
                attempted.update(r["id"] for r in rows)
                inputs = [build_input(idx, r, plans) for r in rows]
                results = list(pool.map(work, inputs))
                with idx:
                    for row, visit, (signals, error) in zip(rows, inputs, results):
                        signals_total += save(idx, visit, row, extractor.name, signals, error)
                        failures += error is not None
                processed += len(rows)
                log.info("extracted %d visits (%d signals so far)", processed, signals_total)

        return {
            "extractor": extractor.name,
            "visits_processed": processed,
            "signals_found": signals_total,
            "failures": failures,
            "total_signals": idx.execute("SELECT COUNT(*) FROM visit_signals").fetchone()[0],
            "unverified_evidence": idx.execute(
                "SELECT COUNT(*) FROM visit_signals WHERE evidence_verified = 0").fetchone()[0],
        }
    finally:
        idx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured signals from visit notes.")
    parser.add_argument("--extractor", choices=["auto", "llm", "keyword"], default="auto")
    parser.add_argument("--max-visits", type=int, default=None, help="stop after N visits (for a cheap trial)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = run(make_extractor(args.extractor), max_visits=args.max_visits)
    for key, value in summary.items():
        print(f"  {key:<20} {value}")


if __name__ == "__main__":
    main()
