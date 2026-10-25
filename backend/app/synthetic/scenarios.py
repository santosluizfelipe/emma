"""Scenarios: what happens to one person over the period, with the truth attached.

Each scenario returns events keyed by (day index, slot). The builder turns them into
visit notes, so every signal in the dataset is known by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# A decline escalates across consecutive mornings: mild first, serious later.
DECLINE_SEVERITY = ["low", "low", "medium", "medium", "medium", "high", "medium"]


@dataclass
class Event:
    category: str
    severity: str
    variant: int = 0
    in_task: Optional[str] = None      # put the sentence in this task's comment instead of the note
    contradicts: bool = False          # task stays "Completed" although the comment says otherwise


@dataclass
class Scenario:
    kind: str
    focus: List[str] = field(default_factory=list)        # categories, drives the care plan
    events: Dict[Tuple[int, str], List[Event]] = field(default_factory=dict)
    traps: Dict[Tuple[int, str], int] = field(default_factory=dict)   # (day, slot) -> trap index
    missing_comments: List[Tuple[int, str, str]] = field(default_factory=list)  # (day, slot, task)
    safeguarding: Optional[Tuple[int, str]] = None        # (day, category label) -> enquiry form
    lunch_visits: bool = False

    def add(self, day: int, slot: str, event: Event) -> None:
        self.events.setdefault((day, slot), []).append(event)


def _pick(severity: str, category: str) -> str:
    """Closest severity that exists for the category."""
    from app.synthetic.text import EVENTS
    for s in [severity, "medium", "low", "high"]:
        if (category, s) in EVENTS:
            return s
    raise KeyError(category)


def routine() -> Scenario:
    return Scenario(kind="routine")


def gradual_decline(domains: List[str], start_day: int = 7, days: int = 7) -> Scenario:
    """Mild changes that build up over a week, cycling through the given domains."""
    s = Scenario(kind="gradual_decline", focus=list(domains), lunch_visits=True)
    from app.synthetic.text import EVENTS
    for i in range(days):
        wanted = DECLINE_SEVERITY[min(i, len(DECLINE_SEVERITY) - 1)]
        category = domains[i % len(domains)]
        if wanted == "high":  # the serious day: use a domain that has a high-severity form
            category = next((d for d in domains if (d, "high") in EVENTS), category)
        s.add(start_day + i, "morning", Event(category, _pick(wanted, category), variant=i % 2))
    return s


def single_event(kind: str, day: int) -> Scenario:
    """One serious event. Bruising, missing money and missed medication also open a
    safeguarding enquiry whose form names the person but has no service_user_id."""
    if kind == "near_fall":
        s = Scenario(kind=kind, focus=["falls_mobility"])
        s.add(day, "morning", Event("falls_mobility", "high"))
    elif kind == "bruising":
        s = Scenario(kind=kind, focus=["skin_injury"], safeguarding=(day, "Physical - unexplained bruising"))
        s.add(day, "morning", Event("skin_injury", "high"))
    elif kind == "money":
        s = Scenario(kind=kind, focus=["safeguarding"], safeguarding=(day, "Financial abuse - concern raised"))
        s.add(day, "morning", Event("safeguarding", "high"))
    elif kind == "medication_missed":
        s = Scenario(kind=kind, focus=["medication"], safeguarding=(day, "Neglect - medication not administered"))
        s.add(day, "morning", Event("medication", "high", in_task="Medication", contradicts=True))
    else:
        raise ValueError(kind)
    return s


def negation_traps(days: List[int]) -> Scenario:
    """Routine visits whose notes mention risk words in a negated way."""
    s = Scenario(kind="negation_traps")
    for i, day in enumerate(days):
        s.traps[(day, "morning")] = i
    return s


def documentation_gaps(n: int, start_day: int = 2) -> Scenario:
    """n tasks marked Completed with no comment, spread over evening visits."""
    s = Scenario(kind="documentation_gaps")
    for i in range(n):
        s.missing_comments.append((start_day + i // 2, "evening", ["Evening Meal", "Continence Care"][i % 2]))
    return s
