"""Write a synthetic care database (same schema as care_data.db) plus its ground truth.

    from app.synthetic import build_dataset, scenarios as sc
    truth = build_dataset("/tmp/demo.db", [sc.routine(), sc.gradual_decline(["respiratory"])])

The truth (signals per visit, safeguarding links, documentation gaps, contradictions)
is written next to the database as <name>.truth.json.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

from app.synthetic import text as T
from app.synthetic.scenarios import Event, Scenario

SOURCE_SCHEMA = """
CREATE TABLE org_units(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE service_users(id INTEGER PRIMARY KEY, name TEXT, date_of_birth TEXT, sex TEXT, status TEXT, org_unit_id INTEGER);
CREATE TABLE carers(id INTEGER PRIMARY KEY, name TEXT, status TEXT, org_unit_id INTEGER);
CREATE TABLE form_types(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE form_configs(id INTEGER PRIMARY KEY, name TEXT, form_type_id INTEGER);
CREATE TABLE visits(id INTEGER PRIMARY KEY, service_user_id INTEGER, carer_id INTEGER, start_time TEXT, end_time TEXT, status TEXT, activities TEXT, note TEXT);
CREATE TABLE forms(id INTEGER PRIMARY KEY, service_user_id INTEGER, org_unit_id INTEGER, form_config_id INTEGER, entity_type TEXT, instance_title TEXT, form_data TEXT, created_at TEXT);
"""


@dataclass
class TruthSignal:
    visit_id: int
    service_user_id: int
    day: str
    category: str
    severity: str
    evidence: str
    source: str            # note | activity_comment
    escalated: bool
    contradicts_status: bool


@dataclass
class Truth:
    as_of: str
    people: Dict[int, dict] = field(default_factory=dict)          # id -> {name, scenario}
    signals: List[TruthSignal] = field(default_factory=list)
    safeguarding_links: Dict[int, int] = field(default_factory=dict)  # form_id -> service_user_id
    completed_without_comment: List[List] = field(default_factory=list)  # [visit_id, task]
    contradictions: List[int] = field(default_factory=list)
    trap_visits: List[int] = field(default_factory=list)

    def to_json(self) -> dict:
        return {**asdict(self), "people": {str(k): v for k, v in self.people.items()},
                "safeguarding_links": {str(k): v for k, v in self.safeguarding_links.items()}}

    @classmethod
    def load(cls, path) -> "Truth":
        raw = json.loads(Path(path).read_text())
        return cls(
            as_of=raw["as_of"],
            people={int(k): v for k, v in raw["people"].items()},
            signals=[TruthSignal(**s) for s in raw["signals"]],
            safeguarding_links={int(k): v for k, v in raw["safeguarding_links"].items()},
            completed_without_comment=raw["completed_without_comment"],
            contradictions=raw["contradictions"],
            trap_visits=raw["trap_visits"],
        )

    def signals_for(self, service_user_id: int) -> List[TruthSignal]:
        return [s for s in self.signals if s.service_user_id == service_user_id]


def truth_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.stem + ".truth.json")


def _names(n: int, rng: random.Random) -> List[tuple]:
    combos = [(f, sex, l) for f, sex in T.FIRST_NAMES for l in T.LAST_NAMES]
    rng.shuffle(combos)
    if n > len(combos):
        # beyond the name bank, add a numeric suffix so names stay unique
        combos += [(f, sex, f"{l}-{i}") for i, (f, sex, l) in enumerate(combos * (n // len(combos)))]
    return combos[:n]


def _care_plan(person: dict, focus: List[str]) -> list:
    conds = [T.CONDITIONS[c][0] for c in focus if c in T.CONDITIONS] or ["no major diagnoses"]
    risks = [T.CONDITIONS[c][1] for c in focus if c in T.CONDITIONS] or ["no specific risks identified"]
    first = person["first"]
    return [
        {"section": "Personal Details", "items": [
            {"question": "Full name", "answer": person["name"]},
            {"question": "Date of birth", "answer": person["dob"]},
            {"question": "Preferred name", "answer": first}]},
        {"section": "Health", "items": [
            {"question": "Health conditions and diagnoses", "answer": f"{first} has {', '.join(conds)}."},
            {"question": "Medication summary", "answer": f"{first} takes medication as prescribed."}]},
        {"section": "Risks & Preferences", "items": [
            {"question": "Known risks", "answer": f"Known risks include {', '.join(risks)}."}]},
    ]


def _moving_and_handling(person: dict) -> list:
    return [
        {"section": "Assessment", "items": [
            {"question": "Current mobility level", "answer": f"{person['first']} mobilises short distances with a frame."}]},
        {"section": "Risk", "items": [
            {"question": "Number of carers required", "answer": "One carer; a second if unsteady."}]},
    ]


def build_dataset(
    path,
    scenarios: List[Scenario],
    start: str = "2025-11-03",
    days: int = 14,
    seed: int = 7,
    org_unit_name: str = "Synthetic Care Team",
    org_units: int = 1,
) -> Truth:
    """Write the database at `path` (overwritten) and return its ground truth.

    With org_units > 1, people and carers are spread round-robin across that many teams;
    each person's forms and carers belong to their team.
    """
    rng = random.Random(seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for p in (path, truth_path(path)):
        if p.exists():
            p.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(SOURCE_SCHEMA)
    conn.executemany("INSERT INTO org_units VALUES (?, ?)",
                     [(u, org_unit_name if u == 1 else f"{org_unit_name} {u}") for u in range(1, org_units + 1)])
    conn.executemany("INSERT INTO form_types VALUES (?, ?)",
                     [(1, "Care Plans"), (2, "Risk Assessments"), (3, "Safeguarding/Complaints")])
    conn.executemany("INSERT INTO form_configs VALUES (?, ?, ?)",
                     [(1, "Care Plan", 1), (2, "Moving and Handling", 2), (3, "Safeguarding Enquiry Report", 3)])

    n_carers = max(4, org_units, min(len(T.CARER_NAMES), len(scenarios) // 3 + 3))
    carers = list(range(1, n_carers + 1))
    conn.executemany("INSERT INTO carers VALUES (?, ?, 'Active', ?)",
                     [(i, T.CARER_NAMES[(i - 1) % len(T.CARER_NAMES)] + ("" if i <= len(T.CARER_NAMES) else f" {i}"),
                       (i - 1) % org_units + 1)
                      for i in carers])

    d0 = date.fromisoformat(start)
    truth = Truth(as_of="")
    visit_id = form_id = sg_number = 0

    for su_id, (scenario, (first, sex, last)) in enumerate(zip(scenarios, _names(len(scenarios), rng)), start=1):
        pron, obj, pos = ("he", "him", "his") if sex == "M" else ("she", "her", "her")
        person = {"name": f"{first} {last}", "first": first,
                  "dob": f"19{rng.randint(30, 48)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"}
        unit = (su_id - 1) % org_units + 1
        unit_carers = [c for c in carers if (c - 1) % org_units + 1 == unit]
        conn.execute("INSERT INTO service_users VALUES (?, ?, ?, ?, 'Active', ?)",
                     (su_id, person["name"], person["dob"], sex, unit))
        truth.people[su_id] = {"name": person["name"], "scenario": scenario.kind}
        fill = dict(first=first, pron=pron, obj=obj, pos=pos)

        for form_config, title, data in ((1, "Care Plan", _care_plan(person, scenario.focus)),
                                         (2, "Moving and Handling", _moving_and_handling(person))):
            form_id += 1
            conn.execute("INSERT INTO forms VALUES (?, ?, ?, ?, 'service_user', ?, ?, ?)",
                         (form_id, su_id, unit, form_config, f"{title} — {person['name']}", json.dumps(data), start))

        slots = ["morning", "lunch", "evening"] if scenario.lunch_visits else ["morning", "evening"]
        for day in range(days):
            day_str = (d0 + timedelta(days=day)).isoformat()
            for slot in slots:
                visit_id += 1
                start_t, end_t = T.SLOT_TIMES[slot]
                events: List[Event] = scenario.events.get((day, slot), [])
                trap = scenario.traps.get((day, slot))

                tasks = []
                for name in T.ACTIVITIES[slot]:
                    missing = (day, slot, name) in scenario.missing_comments
                    tasks.append({"name": name, "status": "Completed", "closed_date": f"{day_str} {start_t}",
                                  "description": f"Support with {name.lower()}.", "is_medication": name == "Medication",
                                  "comment": None if missing else T.ROUTINE_COMMENTS[name]})
                    if missing:
                        truth.completed_without_comment.append([visit_id, name])

                sentences = [rng.choice(T.ROUTINE_OPENERS).format(slot=slot, slot_title=slot.title(), **fill),
                             rng.choice(T.ROUTINE_BODY).format(**fill)]
                for ev in events:
                    bank = T.EVENTS.get((ev.category, ev.severity))
                    if ev.in_task:
                        evidence = T.MEDICATION_NOT_GIVEN
                        for t in tasks:
                            if t["name"] == ev.in_task:
                                t["comment"] = evidence
                        sentences.append("A medication issue is recorded in the task notes.")
                        source = "activity_comment"
                    else:
                        evidence = bank[ev.variant % len(bank)].format(**fill)
                        evidence = evidence[0].upper() + evidence[1:]
                        sentences.append(evidence)
                        source = "note"
                    escalated = any(w in evidence.lower() for w in ("office", "gp", "escalated", "reported", "flagged"))
                    truth.signals.append(TruthSignal(visit_id, su_id, day_str, ev.category, ev.severity, evidence,
                                                     source, escalated, ev.contradicts))
                    if ev.contradicts:
                        truth.contradictions.append(visit_id)
                if trap is not None:
                    sentence = T.TRAPS[trap % len(T.TRAPS)].format(**fill)
                    sentences.append(sentence[0].upper() + sentence[1:])
                    truth.trap_visits.append(visit_id)
                if not events:
                    sentences.append(rng.choice(T.ROUTINE_CLOSERS))

                conn.execute(
                    "INSERT INTO visits VALUES (?, ?, ?, ?, ?, 'Completed', ?, ?)",
                    (visit_id, su_id, rng.choice(unit_carers), f"{day_str} {start_t}", f"{day_str} {end_t}",
                     json.dumps(tasks), " ".join(sentences)),
                )

        if scenario.safeguarding:
            day, category = scenario.safeguarding
            form_id += 1
            sg_number += 1
            day_str = (d0 + timedelta(days=day)).isoformat()
            data = [
                {"section": "Incident", "items": [
                    {"question": "Reference", "answer": f"SG-SYN-{sg_number:03d}"},
                    {"question": "Category", "answer": category},
                    {"question": "Date raised", "answer": day_str},
                    {"question": "Service user involved", "answer": person["name"]}]},
                {"section": "Detail", "items": [
                    {"question": "What was observed or disclosed", "answer": "Concern recorded during a visit."},
                    {"question": "Immediate action taken", "answer": "Enquiry opened."},
                    {"question": "Referrals made", "answer": "Referral to the local authority safeguarding team."},
                    {"question": "Current status", "answer": "Enquiry open."}]},
            ]
            conn.execute("INSERT INTO forms VALUES (?, NULL, ?, 3, 'organisation', ?, ?, ?)",
                         (form_id, unit, f"Safeguarding Enquiry Report SG-SYN-{sg_number:03d}", json.dumps(data), day_str))
            truth.safeguarding_links[form_id] = su_id

    truth.as_of = conn.execute("SELECT MAX(start_time) FROM visits").fetchone()[0]
    conn.commit()
    conn.close()
    truth_path(path).write_text(json.dumps(truth.to_json(), indent=1))
    return truth


def demo_population(n: int, seed: int = 7, days: int = 14) -> List[Scenario]:
    """A realistic mix for scale tests and demos: mostly routine, some declines and events."""
    from app.synthetic import scenarios as sc

    rng = random.Random(seed)
    decline_sets = [["cognition", "intake", "falls_mobility", "mood"], ["respiratory", "intake"],
                    ["falls_mobility", "cognition"], ["intake", "mood"]]
    out: List[Scenario] = []
    for i in range(n):
        r = rng.random()
        if r < 0.08:
            out.append(sc.gradual_decline(rng.choice(decline_sets), start_day=max(0, days - 7)))
        elif r < 0.14:
            out.append(sc.single_event(rng.choice(["near_fall", "bruising", "money", "medication_missed"]),
                                       day=rng.randrange(days)))
        elif r < 0.20:
            out.append(sc.negation_traps([rng.randrange(days) for _ in range(2)]))
        elif r < 0.25:
            out.append(sc.documentation_gaps(rng.randint(1, 3), start_day=rng.randrange(max(1, days - 2))))
        else:
            out.append(sc.routine())
    return out


def standard_scenarios() -> List[Scenario]:
    """The fixed scenario set the test suite runs on: one of each pattern."""
    from app.synthetic import scenarios as sc

    return [
        sc.routine(),
        sc.routine(),
        sc.gradual_decline(["cognition", "intake", "falls_mobility", "mood"], start_day=7),
        sc.gradual_decline(["respiratory", "intake"], start_day=7),
        sc.single_event("bruising", day=3),
        sc.single_event("medication_missed", day=6),
        sc.single_event("money", day=9),
        sc.single_event("near_fall", day=12),
        sc.negation_traps([2, 5, 8, 11]),
        sc.documentation_gaps(5),
    ]
