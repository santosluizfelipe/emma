"""Step 3b: daily and 7-day rolling risk per service user, plus alerts.

  score       = sum of severity weights of that day's signals (low 1, medium 2, high 3)
  rolling_7d  = sum of score over the last 7 days
  alert       = immediate  if any high-severity signal that day
                trend      if rolling_7d >= TREND_ALERT_THRESHOLD
                none       otherwise

The trend alert is what catches slow deteriorations where no single note is
alarming. "Who is at risk?" then becomes one indexed read of this table.
Recomputed over a trailing window, so the work per run is constant.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from app import config, db
from app.ingest.rules import window_start


def run(idx: sqlite3.Connection) -> dict:
    as_of_day = db.day_of(db.get_as_of(idx))
    start = window_start(as_of_day, config.RECOMPUTE_WINDOW_DAYS)
    lookback = window_start(start, config.ROLLING_DAYS - 1)  # signals needed for the first day's 7-day sum

    signals = idx.execute(
        """
        SELECT service_user_id, day, category, severity_score, visit_id
        FROM visit_signals
        WHERE day >= ? AND day <= ? AND evidence_verified = 1
        """,
        (lookback, as_of_day),
    ).fetchall()

    by_person: dict = defaultdict(lambda: defaultdict(list))
    for s in signals:
        by_person[s["service_user_id"]][s["day"]].append(s)

    # Every service user with a visit in the window gets a row per day (zeros included),
    # so "how is X doing" can show a calm baseline, not just bad days.
    people = [r[0] for r in idx.execute(
        "SELECT DISTINCT service_user_id FROM visits WHERE start_time >= ?", (start,)
    )]
    days = []
    d = date.fromisoformat(start)
    while d.isoformat() <= as_of_day:
        days.append(d.isoformat())
        d += timedelta(days=1)

    rows = []
    for su in people:
        daily = by_person.get(su, {})
        for day in days:
            today = daily.get(day, [])
            window_days = [(date.fromisoformat(day) - timedelta(days=i)).isoformat()
                           for i in range(config.ROLLING_DAYS)]
            window = [s for wd in window_days for s in daily.get(wd, [])]
            score = sum(s["severity_score"] for s in today)
            rolling = sum(s["severity_score"] for s in window)
            if any(s["severity_score"] == config.SEVERITY_WEIGHTS["high"] for s in today):
                alert = "immediate"
            elif rolling >= config.TREND_ALERT_THRESHOLD:
                alert = "trend"
            else:
                alert = "none"
            categories = sorted({s["category"] for s in window})
            top = sorted(window, key=lambda s: (-s["severity_score"], s["day"]), reverse=False)
            top_refs = []
            for s in top:
                ref = f"V{s['visit_id']}"
                if ref not in top_refs:
                    top_refs.append(ref)
            rows.append((su, day, score, rolling, json.dumps(categories), json.dumps(top_refs[:5]), alert))

    with idx:
        idx.execute("DELETE FROM su_daily_risk WHERE day >= ?", (start,))
        idx.executemany("INSERT OR REPLACE INTO su_daily_risk VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    alerts = idx.execute(
        "SELECT alert, COUNT(*) FROM su_daily_risk WHERE day = ? GROUP BY alert", (as_of_day,)
    ).fetchall()
    return {"rows": len(rows), "as_of_day": as_of_day, "alerts_today": dict(alerts)}
