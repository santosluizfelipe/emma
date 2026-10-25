"""Layer 3: agent answer checks whose expectations are computed from the data.

Nothing here hard-codes a visit ID or a name: every expectation is a query over the
derived store, so the same questions can be asked of the sample, a synthetic world
of any size, or a production snapshot.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Callable, Dict, List

CITE = re.compile(r"\[([VF]\d+)\]")
REASSURING = ["no concern", "no signal", "no risk", "routine", "settled", "stable", "nothing of concern",
              "no issues", "doing well", "no changes", "no new"]
NOT_FOUND = ["no record", "not find", "couldn't find", "could not find", "no service user", "not found",
             "no match", "don't have", "do not have", "no one", "unable to find"]
SAYS_NONE = re.compile(r"\bno (falls?|recorded falls?|reported falls?)\b|nobody (has )?(had a )?fall|"
                       r"there (were|have been) no falls|no one has (had a )?fall", re.I)


def days_ago(as_of: str, n: int) -> str:
    return (date.fromisoformat(as_of[:10]) - timedelta(days=n - 1)).isoformat()


def first_name(name: str) -> str:
    return name.split()[0]


def mentioned(answer: str, name: str) -> bool:
    low = answer.lower()
    return name.lower() in low or re.search(rf"\b{re.escape(first_name(name).lower())}\b", low) is not None


def _people(conn, ids) -> Dict[int, str]:
    if not ids:
        return {}
    rows = conn.execute(f"SELECT id, name FROM service_users WHERE id IN ({','.join('?' * len(ids))})", list(ids))
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- checks

def alerted_people(answer, cited, tools, conn, as_of, case) -> List[str]:
    """Who should I worry about: the top-K people by alert/score this week must be named, in order."""
    k = case.get("top", 3)
    rows = conn.execute(
        """
        SELECT service_user_id,
               MAX(CASE alert WHEN 'immediate' THEN 2 WHEN 'trend' THEN 1 ELSE 0 END) AS worst,
               MAX(CASE WHEN day = ? THEN rolling_7d END) AS now
        FROM su_daily_risk WHERE day BETWEEN ? AND ?
        GROUP BY service_user_id HAVING worst > 0
        ORDER BY worst DESC, now DESC LIMIT ?
        """, (as_of[:10], days_ago(as_of, 7), as_of[:10], k)).fetchall()
    names = _people(conn, [r[0] for r in rows])
    failures = [f"did not mention {names[r[0]]}" for r in rows if not mentioned(answer, names[r[0]])]
    if "risk_overview" not in tools:
        failures.append("did not call risk_overview")
    if not cited:
        failures.append("no citations")
    return failures


def person_summary(answer, cited, tools, conn, as_of, case) -> List[str]:
    """How is X doing: only X's records; their most serious recent signal; calm if none."""
    su = case["service_user_id"]
    failures = []
    visits = [int(r[1:]) for r in cited if r.startswith("V")]
    if visits:
        others = conn.execute(f"SELECT id FROM visits WHERE id IN ({','.join('?' * len(visits))}) "
                              "AND service_user_id <> ?", visits + [su]).fetchall()
        if others:
            failures.append(f"cited other people's visits {[f'V{r[0]}' for r in others]}")
    signals = conn.execute(
        "SELECT visit_id, severity FROM visit_signals WHERE service_user_id = ? AND day >= ? AND evidence_verified = 1",
        (su, days_ago(as_of, 14))).fetchall()
    if signals:
        top = max(r[1] == "high" for r in signals)
        wanted = {f"V{r[0]}" for r in signals if (r[1] == "high") == top}
        if not wanted & set(cited):
            failures.append(f"did not cite their most serious recent record (any of {sorted(wanted)[:5]})")
    elif not any(p in answer.lower() for p in REASSURING):
        failures.append("no signals on record, but the answer isn't reassuring")
    return failures


def falls(answer, cited, tools, conn, as_of, case) -> List[str]:
    rows = conn.execute(
        "SELECT visit_id, severity FROM visit_signals WHERE category = 'falls_mobility' AND day >= ? "
        "AND evidence_verified = 1", (days_ago(as_of, 14),)).fetchall()
    high = {f"V{r[0]}" for r in rows if r[1] == "high"}
    failures = []
    if rows and SAYS_NONE.search(answer):
        failures.append("said there were no falls, but falls/near-falls are recorded")
    if high and not high & set(cited):
        failures.append(f"cited none of the recorded falls {sorted(high)[:5]}")
    if not rows and not SAYS_NONE.search(answer) and not any(p in answer.lower() for p in REASSURING):
        failures.append("no falls recorded, but the answer doesn't say so")
    return failures


def safeguarding(answer, cited, tools, conn, as_of, case) -> List[str]:
    forms = [f"F{r[0]}" for r in conn.execute(
        "SELECT id FROM forms WHERE form_type = 'Safeguarding/Complaints' ORDER BY created_at DESC")]
    need = min(len(forms), case.get("min_cited", 5))
    got = set(forms) & set(cited)
    failures = []
    if len(got) < need:
        failures.append(f"cited {len(got)} safeguarding forms, expected at least {need}")
    if "safeguarding_cases" not in tools:
        failures.append("did not call safeguarding_cases")
    return failures


def completed_without_comment(answer, cited, tools, conn, as_of, case) -> List[str]:
    n = conn.execute("SELECT COUNT(*) FROM record_issues WHERE issue = 'completed_without_comment' AND day >= ?",
                     (days_ago(as_of, 14),)).fetchone()[0]
    return [] if re.search(rf"\b{n}\b", answer) else [f"did not state the count ({n})"]


def carer_counts(answer, cited, tools, conn, as_of, case) -> List[str]:
    rows = conn.execute(
        "SELECT c.name, COUNT(*) FROM visits v JOIN carers c ON c.id = v.carer_id "
        "WHERE v.start_time >= ? GROUP BY c.name", (days_ago(as_of, 7),)).fetchall()
    lines = answer.lower().splitlines()
    failures = [f"missing or wrong: {name} = {n}" for name, n in rows
                if not any(first_name(name).lower() in line and re.search(rf"\b{n}\b", line) for line in lines)]
    if "query_sql" not in tools:
        failures.append("did not call query_sql")
    return failures


def unknown_person(answer, cited, tools, conn, as_of, case) -> List[str]:
    failures = []
    if cited:
        failures.append(f"cited records for a person who doesn't exist: {cited[:5]}")
    if not any(p in answer.lower() for p in NOT_FOUND):
        failures.append("didn't say the person wasn't found")
    return failures


CHECKS: Dict[str, Callable[..., List[str]]] = {
    "alerted_people": alerted_people,
    "person_summary": person_summary,
    "falls": falls,
    "safeguarding": safeguarding,
    "completed_without_comment": completed_without_comment,
    "carer_counts": carer_counts,
    "unknown_person": unknown_person,
}


def universal(answer: str, cited: List[str], removed: List[str], conn: sqlite3.Connection) -> List[str]:
    """Invariants for every answer, whatever the question."""
    failures = []
    if removed:
        failures.append(f"invented citations had to be removed: {removed}")
    for ref in set(cited):
        table = "visits" if ref[0] == "V" else "forms"
        if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(ref[1:]),)).fetchone():
            failures.append(f"cited {ref}, which doesn't exist")
    if not answer.strip():
        failures.append("empty answer")
    return failures


def expand_people(conn, as_of: str, mode: str) -> List[dict]:
    """Pick whom to ask about: the 2 highest-risk people and 2 with no signals at all."""
    risky = conn.execute(
        "SELECT service_user_id FROM su_daily_risk WHERE day = ? AND rolling_7d > 0 ORDER BY rolling_7d DESC LIMIT 2",
        (as_of[:10],)).fetchall()
    calm = conn.execute(
        "SELECT id FROM service_users WHERE id NOT IN (SELECT service_user_id FROM visit_signals) ORDER BY id LIMIT 2"
    ).fetchall()
    ids = [r[0] for r in risky] + [r[0] for r in calm]
    names = _people(conn, ids)
    return [{"service_user_id": i, "name": names[i]} for i in ids]
