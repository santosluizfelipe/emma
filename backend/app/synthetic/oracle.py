"""An extractor that returns the ground truth for synthetic data.

Downstream logic (rules, roll-ups, tools, agent) is tested with perfect extraction,
so a test failure points at that logic, not at the extractor. Extractor quality is
measured separately (eval/cases/extraction).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from app.ingest.extractors import Extractor, VisitInput
from app.ingest.signals import ExtractedSignal
from app.synthetic.builder import Truth


class OracleExtractor(Extractor):
    name = "oracle-v1"

    def __init__(self, truth: Truth):
        self.by_visit: Dict[int, List] = defaultdict(list)
        for s in truth.signals:
            self.by_visit[s.visit_id].append(s)

    def extract(self, visit: VisitInput) -> List[ExtractedSignal]:
        return [
            ExtractedSignal(category=s.category, severity=s.severity, evidence=s.evidence,
                            source=s.source, escalated=s.escalated)
            for s in self.by_visit.get(visit.visit_id, [])
        ]
