"""The signal taxonomy shared by both extractors."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "falls_mobility",   # falls, near-falls, unsteadiness
    "cognition",        # confusion, disorientation
    "intake",           # eating, drinking, appetite
    "respiratory",      # breathlessness, cough, inhaler use
    "skin_injury",      # bruising, wounds, pressure areas
    "medication",       # missed, refused or not given
    "mood",             # withdrawn, low, distressed, anxious
    "pain",
    "continence",
    "safeguarding",     # possible abuse, neglect, financial concerns
    "other",
]
Severity = Literal["low", "medium", "high"]
CATEGORIES = list(Category.__args__)  # type: ignore[attr-defined]


class ExtractedSignal(BaseModel):
    """One observation worth a care manager's attention."""

    category: Category
    severity: Severity
    evidence: str = Field(description="Exact quote copied from the record, one sentence or less")
    source: Literal["note", "activity_comment"]
    escalated: bool = Field(description="The record says it was reported/escalated/flagged")


class ExtractionResult(BaseModel):
    signals: List[ExtractedSignal]
