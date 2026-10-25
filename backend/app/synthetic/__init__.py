"""Synthetic care data with ground truth, for tests, evaluations and scale checks.

The labels come from the generator: every risk sentence is planted on purpose, so
nobody has to hand-label anything, and datasets can be any size.

    python -m app.synthetic --people 200 --out data/synthetic.db
"""

from app.synthetic.builder import Truth, build_dataset, demo_population, standard_scenarios, truth_path
from app.synthetic.oracle import OracleExtractor

__all__ = ["Truth", "build_dataset", "demo_population", "standard_scenarios", "truth_path", "OracleExtractor"]
