"""Generate a synthetic source database you can point SOURCE_DB_PATH at.

    python -m app.synthetic --people 200 --days 14 --out data/synthetic.db
    SOURCE_DB_PATH=data/synthetic.db INDEX_DB_PATH=data/synthetic_index.db python -m app.ingest
"""

from __future__ import annotations

import argparse
from collections import Counter

from app.synthetic.builder import build_dataset, demo_population, standard_scenarios, truth_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--people", type=int, default=0, help="size of a random population (0 = the standard 10-person set)")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="data/synthetic.db")
    args = parser.parse_args()

    scenarios = demo_population(args.people, args.seed, args.days) if args.people else standard_scenarios()
    truth = build_dataset(args.out, scenarios, days=args.days, seed=args.seed)
    kinds = Counter(p["scenario"] for p in truth.people.values())
    print(f"wrote {args.out} and {truth_path(args.out)}")
    print(f"  people: {len(truth.people)}  {dict(kinds)}")
    print(f"  planted signals: {len(truth.signals)}  safeguarding forms: {len(truth.safeguarding_links)}")
    print(f"  as_of: {truth.as_of}")


if __name__ == "__main__":
    main()
