"""Step 3b: daily and 7-day rolling risk per service user, plus alerts.

  score       = sum of severity weights of that day's signals (low 1, medium 2, high 3)
  rolling_7d  = sum of score over the last 7 days
  alert       = immediate  if any high-severity signal that day
                trend      if rolling_7d >= TREND_ALERT_THRESHOLD
                none       otherwise

The trend alert is what catches slow deteriorations where no single note is
alarming. "Who is at risk?" then becomes one indexed read of this table.

Scale properties:
  * Sparse: a row is stored only for days with a score or a rolling score. Someone
    with no recent signals has no rows, so the table grows with risk, not headcount.
  * Incremental: a run recomputes the recent window only for people whose signals
    changed (queued by extract), plus any new days for people whose 7-day window
    still holds signals. The first run of a new version recomputes the whole window.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, List

from app import config, db
from app.ingest.rules import window_start

ROLLUP_VERSION = "2"  # bump when scoring changes: the next run recomputes the window
PEOPLE_CHUNK = 500


def _days(first: str, last: str) -> List[str]:
    out, d = [], date.fromisoformat(first)
    while d.isoformat() <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _rows_for(signals_by_day: dict, su: int, days: List[str]) -> list:
    rows = []
    for day in days:
        today = signals_by_day.get(day, [])
        window = [s for i in range(config.ROLLING_DAYS)
                  for s in signals_by_day.get((date.fromisoformat(day) - timedelta(days=i)).isoformat(), [])]
        if not window:
            continue  # sparse: nothing in the last 7 days, no row
        score = sum(s["severity_score"] for s in today)
        rolling = sum(s["severity_score"] for s in window)
        if any(s["severity_score"] == config.SEVERITY_WEIGHTS["high"] for s in today):
            alert = "immediate"
        elif rolling >= config.TREND_ALERT_THRESHOLD:
            alert = "trend"
        else:
            alert = "none"
        top_refs = []
        for s in sorted(window, key=lambda s: (-s["severity_score"], s["day"])):
            ref = f"V{s['visit_id']}"
            if ref not in top_refs:
                top_refs.append(ref)
        rows.append((su, day, score, rolling, json.dumps(sorted({s["category"] for s in window})),
                     json.dumps(top_refs[:5]), alert))
    return rows


def recompute(idx: sqlite3.Connection, people: Iterable[int], first_day: str, last_day: str) -> int:
    """Replace these people's rows for [first_day, last_day]. Returns rows written."""
    people, written = list(people), 0
    days = _days(first_day, last_day)
    lookback = window_start(first_day, config.ROLLING_DAYS - 1)  # signals the first day's sum needs
    for i in range(0, len(people), PEOPLE_CHUNK):
        chunk = people[i:i + PEOPLE_CHUNK]
        by_person: dict = defaultdict(lambda: defaultdict(list))
        for s in idx.execute(
            f"""
            SELECT service_user_id, day, category, severity_score, visit_id
            FROM visit_signals
            WHERE service_user_id IN ({db.marks(chunk)}) AND day BETWEEN ? AND ? AND evidence_verified = 1
            """,
            [*chunk, lookback, last_day],
        ):
            by_person[s["service_user_id"]][s["day"]].append(s)
        rows = [row for su in chunk for row in _rows_for(by_person.get(su, {}), su, days)]
        idx.execute(f"DELETE FROM su_daily_risk WHERE service_user_id IN ({db.marks(chunk)}) AND day BETWEEN ? AND ?",
                    [*chunk, first_day, last_day])
        idx.executemany("INSERT INTO su_daily_risk VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        written += len(rows)
    return written


def people_with_signals(idx: sqlite3.Connection, first_day: str, last_day: str) -> List[int]:
    return [r[0] for r in idx.execute(
        "SELECT DISTINCT service_user_id FROM visit_signals WHERE day BETWEEN ? AND ? AND evidence_verified = 1",
        (first_day, last_day))]


def run(idx: sqlite3.Connection) -> dict:
    as_of_day = db.day_of(db.get_as_of(idx))
    start = window_start(as_of_day, config.RECOMPUTE_WINDOW_DAYS)
    through = db.get_meta(idx, "rollup:through_day")
    full = (db.get_meta(idx, "rollup:version") != ROLLUP_VERSION or through is None or through > as_of_day)
    people = written = 0

    if full:
        with idx:
            # Whole window for everyone with signals; also drops the zero rows older versions stored
            idx.execute("DELETE FROM su_daily_risk WHERE day >= ? OR (score = 0 AND rolling_7d = 0)", (start,))
            idx.execute("DELETE FROM queue_rollup_people")
            todo = people_with_signals(idx, window_start(start, config.ROLLING_DAYS - 1), as_of_day)
            written += recompute(idx, todo, start, as_of_day)
            people += len(todo)
            db.set_meta(idx, "rollup:version", ROLLUP_VERSION)
            db.set_meta(idx, "rollup:through_day", as_of_day)
    else:
        changed: List[int] = []
        first_new = (date.fromisoformat(through) + timedelta(days=1)).isoformat()
        redo_from = min(start, first_new)  # a long gap since the last run reaches back past the window

        def redo(ids: List[int]) -> None:  # people whose signals changed: their whole recent window
            nonlocal written
            changed.extend(ids)
            written += recompute(idx, ids, redo_from, as_of_day)

        people += db.drain(idx, "queue_rollup_people", redo)
        with idx:
            if through < as_of_day:  # new days since the last run, for whoever still has signals in range
                done = set(changed)
                todo = [su for su in people_with_signals(idx, window_start(first_new, config.ROLLING_DAYS - 1), as_of_day)
                        if su not in done]
                written += recompute(idx, todo, first_new, as_of_day)
                people += len(todo)
            db.set_meta(idx, "rollup:through_day", as_of_day)

    alerts = idx.execute(
        "SELECT alert, COUNT(*) FROM su_daily_risk WHERE day = ? GROUP BY alert", (as_of_day,)
    ).fetchall()
    return {"mode": "full" if full else "incremental", "people_recomputed": people, "rows_written": written,
            "as_of_day": as_of_day, "alerts_today": dict(alerts)}
