"""CLI: python -m app.ingest.normalise [--full]"""

from __future__ import annotations

import argparse
import logging

from app.ingest.normalise import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten the source data into the derived store.")
    parser.add_argument("--full", action="store_true", help="rebuild from scratch")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    summary = run(full=args.full)
    width = max(len(k) for k in summary)
    print("\nNormalisation complete")
    for key, value in summary.items():
        print(f"  {key:<{width}}  {value}")


if __name__ == "__main__":
    main()
