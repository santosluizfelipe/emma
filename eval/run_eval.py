"""Evaluate the system. Run from backend/ with the venv active.

    python ../eval/run_eval.py extraction                  # keyword baseline (free) + LLM if a key is set
    python ../eval/run_eval.py extraction --extractor llm
    python ../eval/run_eval.py agent                       # the data you built with `python -m app.ingest`
    python ../eval/run_eval.py agent --dataset synthetic --people 60
    python ../eval/run_eval.py all

Layers (see eval/README.md):
  extraction  pattern cases in eval/cases/extraction/*.yaml, no database needed
  agent       questions in eval/cases/agent.yaml, expectations computed from the data
Invariants (layer 4) are checked on every agent answer and in backend/tests.

Writes eval/results/<run>.md/.json (extraction, agent-current, agent-synthetic, all)
plus latest.md. Exit code 1 if anything fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

import _paths
import agent_eval
import extraction_eval
from app import config


# --------------------------------------------------------------------------- extraction

def run_extraction(which: str, log=print) -> List[dict]:
    from app.ingest.extractors import KeywordExtractor, LLMExtractor

    cases = extraction_eval.load_cases()
    extractors = []
    if which in ("keyword", "both"):
        extractors.append(KeywordExtractor())
    if which in ("llm", "both"):
        if config.OPENAI_API_KEY:
            extractors.append(LLMExtractor())
        else:
            log("  (LLM extractor skipped: OPENAI_API_KEY not set)")
    out = []
    for ex in extractors:
        results = extraction_eval.run(ex, cases)
        summary = extraction_eval.summarise(results)
        log(f"\n  {ex.name}: {summary['passed']}/{summary['cases']} cases passed, "
            f"negatives clean {summary['negatives_clean']}, errors {summary['errors']}")
        for r in results:
            if not r.passed:
                log(f"    FAIL {r.case['file']}:{r.case['id']}: {r.error or '; '.join(r.failures)}")
        out.append({"extractor": ex.name, "summary": summary,
                    "cases": [{"id": r.case["id"], "file": r.case["file"], "passed": r.passed,
                               "failures": r.failures, "error": r.error, "signals": r.signals} for r in results]})
    return out


# --------------------------------------------------------------------------- agent

def build_synthetic(people: int, seed: int):
    from app.search.embeddings import default_embedder
    from app.synthetic.builder import demo_population
    from app.synthetic.pipeline import build_world

    folder = _paths.EVAL_DIR / ".work" / f"synthetic-{people}-{seed}"
    return build_world(folder, demo_population(people, seed), embedder=default_embedder())["index"]


def run_agent(dataset: str, people: int, seed: int, only: Optional[List[str]], log=print) -> dict:
    from app.agent.loop import Agent

    if not config.OPENAI_API_KEY:
        log("  skipped: OPENAI_API_KEY not set")
        return {"dataset": dataset, "skipped": True, "results": []}
    index = build_synthetic(people, seed) if dataset == "synthetic" else None
    agent = Agent(index_path=index)
    log(f"  dataset: {dataset}{f' ({people} people)' if dataset == 'synthetic' else ''}, "
        f"as of {agent.ctx.as_of}, model {agent.model}")
    results = agent_eval.run(agent, agent_eval.load_cases(), only, log)
    return {"dataset": dataset, "skipped": False, "results": results}


# --------------------------------------------------------------------------- report

def write_report(report: dict, name: str) -> None:
    out = _paths.EVAL_DIR / "results"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2, default=str))
    (out / f"{name}.json").write_text(json.dumps(report, indent=2, default=str))
    lines = ["# Evaluation report", "", f"Run: {report['run_at']}", ""]
    if report.get("extraction"):
        lines += ["## Extraction (pattern cases)", "",
                  "| Extractor | Cases passed | Negatives clean | Errors |", "|---|---|---|---|"]
        for e in report["extraction"]:
            s = e["summary"]
            lines.append(f"| {e['extractor']} | {s['passed']}/{s['cases']} | {s['negatives_clean']} | {s['errors']} |")
        for e in report["extraction"]:
            lines += ["", f"**{e['extractor']}** per category", "", "| Category | Recall | Precision |", "|---|---|---|"]
            for cat, v in e["summary"]["per_category"].items():
                rec = f"{v['found']}/{v['expected']}" if v["expected"] else "-"
                prec = f"{v['correct']}/{v['predicted']}" if v["predicted"] else "-"
                lines.append(f"| {cat} | {rec} | {prec} |")
            fails = [c for c in e["cases"] if not c["passed"]]
            if fails:
                lines += ["", "Failures:", ""] + [f"- `{c['id']}`: {c['error'] or '; '.join(c['failures'])}" for c in fails]
    for run in report.get("agent", []):
        if run["skipped"]:
            continue
        lines += ["", f"## Agent ({run['dataset']} data)", "", "| Question | Result | Tools | Seconds | Notes |",
                  "|---|---|---|---|---|"]
        for r in run["results"]:
            lines.append(f"| {r['question']} | {'pass' if r['passed'] else 'FAIL'} | {', '.join(r['tools'])} | "
                         f"{r['seconds']} | {'; '.join(r['failures'])} |")
    for filename in ("latest.md", f"{name}.md"):
        (out / filename).write_text("\n".join(lines) + "\n")
    print(f"\nReport: {out / (name + '.md')} (also latest.md)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_ext = sub.add_parser("extraction")
    p_ext.add_argument("--extractor", choices=["keyword", "llm", "both"], default="both")
    p_agent = sub.add_parser("agent")
    p_agent.add_argument("--dataset", choices=["current", "synthetic"], default="current")
    p_agent.add_argument("--people", type=int, default=60)
    p_agent.add_argument("--seed", type=int, default=7)
    p_agent.add_argument("--only", nargs="*", help="case ids, e.g. worry falls")
    sub.add_parser("all")
    args = parser.parse_args()

    report: dict = {"run_at": time.strftime("%Y-%m-%d %H:%M:%S"), "extraction": [], "agent": []}
    if args.command in ("extraction", "all"):
        print("Extraction")
        report["extraction"] = run_extraction(getattr(args, "extractor", "both"))
    if args.command == "agent":
        print("\nAgent")
        report["agent"].append(run_agent(args.dataset, args.people, args.seed, args.only))
    if args.command == "all":
        for dataset in ("current", "synthetic"):
            print(f"\nAgent ({dataset})")
            report["agent"].append(run_agent(dataset, 60, 7, None))
    name = {"extraction": "extraction", "all": "all"}.get(args.command) or f"agent-{args.dataset}"
    write_report(report, name)

    ext_main = report["extraction"][-1]["summary"] if report["extraction"] else None
    failed = (ext_main is not None and ext_main["passed"] < ext_main["cases"]) or any(
        not r["passed"] for run in report["agent"] for r in run["results"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
