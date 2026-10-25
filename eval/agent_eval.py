"""Layer 3 runner: ask the real agent the questions in eval/cases/agent.yaml and
check the answers against expectations computed from the loaded data."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

import yaml

import _paths  # noqa: F401
import checks
from app import db

CASES_FILE = _paths.EVAL_DIR / "cases" / "agent.yaml"


def load_cases(path: Path = CASES_FILE) -> List[dict]:
    cases = yaml.safe_load(Path(path).read_text())
    for c in cases:
        if c["check"] not in checks.CHECKS:
            raise ValueError(f"{c['id']}: unknown check {c['check']}")
    return cases


def expand(cases: List[dict], conn, as_of: str) -> List[dict]:
    out = []
    for c in cases:
        if "{name}" in c["question"]:
            for p in checks.expand_people(conn, as_of, c.get("people", "risk_and_calm")):
                out.append({**c, **p, "id": f"{c['id']}:{p['name']}", "question": c["question"].format(name=p["name"])})
        else:
            out.append(c)
    return out


def run(agent, cases: List[dict], only: Optional[List[str]] = None, log=print) -> List[dict]:
    conn, as_of = agent.conn, agent.ctx.as_of
    results = []
    for case in expand(cases, conn, as_of):
        if only and case["id"].split(":")[0] not in only:
            continue
        start = time.monotonic()
        try:
            r = agent.ask(case["question"])
            tools = [t["tool"] for t in r.tool_calls]
            cited = checks.CITE.findall(r.answer)
            failures = checks.universal(r.answer, cited, r.removed_citations, conn)
            failures += [f"quoted words not in the cited record (shown unquoted): {q}" for q in r.unverified_quotes]
            failures += checks.CHECKS[case["check"]](r.answer, cited, tools, conn, as_of, case)
            row = {"id": case["id"], "question": case["question"], "passed": not failures, "failures": failures,
                   "tools": tools, "cited": sorted(set(cited)), "answer": r.answer, "tokens": r.tokens}
        except Exception as exc:
            row = {"id": case["id"], "question": case["question"], "passed": False,
                   "failures": [f"error: {type(exc).__name__}: {exc}"], "tools": [], "cited": [], "answer": ""}
        row["seconds"] = round(time.monotonic() - start, 1)
        results.append(row)
        log(f"  {'PASS' if row['passed'] else 'FAIL'}  {row['seconds']:>5}s  {row['question']}")
        for f in row["failures"]:
            log(f"          - {f}")
    return results


def as_of_of(index_path) -> str:
    conn = db.connect_index_readonly(index_path)
    try:
        return db.get_as_of(conn)
    finally:
        conn.close()
