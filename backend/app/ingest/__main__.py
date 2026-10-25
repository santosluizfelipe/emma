"""Run the whole write path:

    python -m app.ingest                       # incremental
    python -m app.ingest --full                # rebuild normalised tables from the source
    python -m app.ingest --extractor keyword   # offline: no API key needed

  1. normalise  - copy + flatten source records
  2. extract    - visit notes -> signals (once per visit, cached by content hash)
  3. rules      - documentation gaps, contradictions, overdue reviews
     rollup     - daily and 7-day risk scores, alerts
  4. index      - keyword index + embeddings for search
"""

from __future__ import annotations

import argparse
import logging
import time

from app import db
from app.ingest import extract, normalise, rollup, rules
from app.search import index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="rebuild normalised tables from scratch")
    parser.add_argument("--extractor", choices=["auto", "llm", "keyword"], default="auto")
    parser.add_argument("--no-embeddings", action="store_true", help="skip vectors (keyword search only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    def step(title, fn):
        start = time.monotonic()
        result = fn()
        print(f"\n[{title}] {time.monotonic() - start:.1f}s")
        for key, value in (result or {}).items():
            print(f"  {key:<22} {value}")

    step("1 normalise", lambda: normalise.run(full=args.full))
    step("2 extract", lambda: extract.run(extract.make_extractor(args.extractor)))

    idx = db.connect_index()
    try:
        step("3 rules", lambda: rules.run(idx))
        step("3 rollup", lambda: rollup.run(idx))
        step("4 index", lambda: index.run(idx, use_default_embedder=not args.no_embeddings))
    finally:
        idx.close()
    print("\nDone. Ask questions with: python -m app.agent  (or run the API: uvicorn app.api:app)")


if __name__ == "__main__":
    main()
