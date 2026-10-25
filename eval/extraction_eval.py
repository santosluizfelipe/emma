"""Layer 2: score an extractor on the pattern cases in eval/cases/extraction/.

Each case is a self-contained visit with expected and forbidden signals, so the
score doesn't depend on any database and the set grows by adding files.
"""

from __future__ import annotations

import glob
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

import _paths  # noqa: F401
from app.ingest.extract import dedupe, evidence_is_verbatim
from app.ingest.extractors import Extractor, VisitInput
from app.ingest.signals import CATEGORIES

CASES_DIR = _paths.EVAL_DIR / "cases" / "extraction"
VALID_SEVERITIES = {"low", "medium", "high"}


def _as_list(value) -> List[str]:
    return list(value) if isinstance(value, list) else [value]


def load_cases(folder: Path = CASES_DIR) -> List[dict]:
    cases, seen = [], set()
    for path in sorted(glob.glob(str(folder / "*.yaml"))):
        for case in yaml.safe_load(Path(path).read_text()) or []:
            case["file"] = Path(path).name
            validate_case(case)
            if case["id"] in seen:
                raise ValueError(f"duplicate case id {case['id']}")
            seen.add(case["id"])
            cases.append(case)
    return cases


def validate_case(case: dict) -> None:
    where = f"{case.get('file')}:{case.get('id')}"
    if not case.get("id") or not case.get("note") or "expect" not in case:
        raise ValueError(f"{where}: id, note and expect are required")
    for item in case["expect"]:
        for c in _as_list(item["category"]):
            if c not in CATEGORIES:
                raise ValueError(f"{where}: unknown category {c}")
        for s in _as_list(item.get("severity", [])):
            if s not in VALID_SEVERITIES:
                raise ValueError(f"{where}: unknown severity {s}")
    for c in case.get("forbid", []):
        if c not in CATEGORIES:
            raise ValueError(f"{where}: unknown forbidden category {c}")


def to_visit(case: dict, i: int) -> VisitInput:
    return VisitInput(
        visit_id=i, service_user_id=0, first_name="Pat", full_name="Pat Doe",
        start_time="2025-01-01 08:00:00", note=case["note"],
        activities=case.get("tasks", []), known_risks=case.get("known_risks", ""),
    )


@dataclass
class CaseResult:
    case: dict
    signals: List[dict]
    failures: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return not self.failures and not self.error


def score_case(case: dict, signals: List[dict]) -> List[str]:
    failures, used = [], set()
    for item in case["expect"]:
        accept = _as_list(item["category"])
        match = next((i for i, s in enumerate(signals) if i not in used and s["category"] in accept), None)
        if match is None:
            failures.append(f"missed {'/'.join(accept)}")
            continue
        used.add(match)
        s = signals[match]
        if "severity" in item and s["severity"] not in _as_list(item["severity"]):
            failures.append(f"{s['category']} severity {s['severity']}, expected {item['severity']}")
        if "escalated" in item and bool(s["escalated"]) != item["escalated"]:
            failures.append(f"{s['category']} escalated={s['escalated']}, expected {item['escalated']}")
    for s in signals:
        if s["category"] in case.get("forbid", []):
            failures.append(f"forbidden {s['category']}: {s['evidence'][:60]}")
    if not case["expect"] and signals:
        failures.append("expected no signals, got " + ", ".join(s["category"] for s in signals))
    for s in signals:
        if not s["verbatim"]:
            failures.append(f"quote not in the note: {s['evidence'][:60]}")
    return failures


def run(extractor: Extractor, cases: List[dict], workers: int = 8) -> List[CaseResult]:
    def one(pair):
        i, case = pair
        visit = to_visit(case, i)
        try:
            found = dedupe(extractor.extract(visit))
        except Exception as exc:  # report, don't stop the run
            return CaseResult(case, [], error=f"{type(exc).__name__}: {exc}")
        signals = [{"category": s.category, "severity": s.severity, "escalated": s.escalated,
                    "evidence": s.evidence, "verbatim": evidence_is_verbatim(s.evidence, visit)} for s in found]
        return CaseResult(case, signals, score_case(case, signals))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, enumerate(cases, start=1)))


def summarise(results: List[CaseResult]) -> dict:
    """Case pass rate, plus recall/precision per category over expected items."""
    per_cat: Dict[str, Dict[str, int]] = defaultdict(lambda: {"expected": 0, "found": 0, "predicted": 0, "correct": 0})
    for r in results:
        for item in r.case["expect"]:
            primary = _as_list(item["category"])[0]
            per_cat[primary]["expected"] += 1
            if any(s["category"] in _as_list(item["category"]) for s in r.signals):
                per_cat[primary]["found"] += 1
        for s in r.signals:
            per_cat[s["category"]]["predicted"] += 1
            if any(s["category"] in _as_list(i["category"]) for i in r.case["expect"]):
                per_cat[s["category"]]["correct"] += 1
    negatives = [r for r in results if not r.case["expect"]]
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "errors": sum(bool(r.error) for r in results),
        "negatives_clean": f"{sum(not r.signals for r in negatives)}/{len(negatives)}",
        "per_category": {k: v for k, v in sorted(per_cat.items())},
    }
