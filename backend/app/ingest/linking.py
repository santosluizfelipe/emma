"""Resolve a person's name written inside a form to a service_users.id.

Safeguarding forms in the source have service_user_id = NULL; the person is only
named in the JSON ("Service user involved: Raymond Clarke"). Linking a
safeguarding case to the wrong person is a serious error, so the rule is:
link when confident, otherwise send it to a human review queue. Never guess.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_MIN_SCORE = 0.90   # how similar the best name must be
FUZZY_MIN_MARGIN = 0.05  # how much better than the runner-up it must be


@dataclass(frozen=True)
class Candidate:
    id: int
    name: str
    org_unit_id: int | None


@dataclass(frozen=True)
class LinkResult:
    service_user_id: int | None
    confidence: float
    method: str  # exact | fuzzy | ambiguous | unmatched
    detail: str = ""


def normalise_name(name: str) -> str:
    """'  Raymond  CLARKE. ' -> 'raymond clarke' (accents and punctuation removed)."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z\s'-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def link_name(name: str, candidates: list[Candidate]) -> LinkResult:
    """Match a written name against candidate service users.

    Callers should pass candidates already scoped to the form's org unit, so a
    form can never be linked to someone in another team or tenant.
    """
    target = normalise_name(name)
    if not target:
        return LinkResult(None, 0.0, "unmatched", "empty name")

    exact = [c for c in candidates if normalise_name(c.name) == target]
    if len(exact) == 1:
        return LinkResult(exact[0].id, 1.0, "exact")
    if len(exact) > 1:
        ids = ", ".join(str(c.id) for c in exact)
        return LinkResult(None, 0.0, "ambiguous", f"several service users named '{name}': {ids}")

    scored = sorted(
        ((SequenceMatcher(None, target, normalise_name(c.name)).ratio(), c) for c in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return LinkResult(None, 0.0, "unmatched", "no candidates in this org unit")

    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= FUZZY_MIN_SCORE and best_score - runner_up >= FUZZY_MIN_MARGIN:
        return LinkResult(best.id, round(best_score, 3), "fuzzy", f"matched '{best.name}'")

    return LinkResult(
        None,
        0.0,
        "unmatched",
        f"best guess '{best.name}' scored {best_score:.2f} (runner-up {runner_up:.2f})",
    )
