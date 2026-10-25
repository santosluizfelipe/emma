"""Step 3a: deterministic checks that need no LLM.

Recomputed over a trailing window (RECOMPUTE_WINDOW_DAYS) on every run, so the
work per run stays constant however much history accumulates.

Issues written to record_issues:
  * completed_without_comment  - task marked Completed with no comment (one row per task)
  * status_contradiction       - the record says medication wasn't given, but the
                                 Medication task is marked Completed
  * assessment_not_reviewed    - a high-severity signal happened after the person's
                                 latest care plan / risk assessment
  * unverified_evidence        - an extracted quote couldn't be found in the source
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app import config, db


def window_start(as_of: str, days: int) -> str:
    return (date.fromisoformat(db.day_of(as_of)) - timedelta(days=days)).isoformat()


def run(idx: sqlite3.Connection) -> dict:
    as_of = db.get_as_of(idx)
    start = window_start(as_of, config.RECOMPUTE_WINDOW_DAYS)

    with idx:
        idx.execute("DELETE FROM record_issues WHERE day >= ?", (start,))

        idx.execute(
            """
            INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
            SELECT 'visit', v.id, v.service_user_id, substr(v.start_time, 1, 10),
                   'completed_without_comment', a.name
            FROM visit_activities a
            JOIN visits v ON v.id = a.visit_id
            WHERE v.start_time >= ? AND a.status = 'Completed'
              AND (a.comment IS NULL OR trim(a.comment) = '')
            """,
            (start,),
        )

        idx.execute(
            """
            INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
            SELECT 'visit', s.visit_id, s.service_user_id, s.day, 'status_contradiction',
                   'Medication task marked Completed; record says: ' || s.evidence
            FROM visit_signals s
            WHERE s.day >= ? AND s.contradicts_status = 1
            """,
            (start,),
        )

        idx.execute(
            """
            INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
            SELECT 'signal', s.id, s.service_user_id, s.day, 'unverified_evidence',
                   'Quote not found in V' || s.visit_id || ': ' || s.evidence
            FROM visit_signals s
            WHERE s.day >= ? AND s.evidence_verified = 0
            """,
            (start,),
        )

        # Latest high-severity signal per person vs their latest care plan / risk assessment
        idx.execute(
            """
            INSERT OR REPLACE INTO record_issues (record_type, record_id, service_user_id, day, issue, detail)
            WITH latest_high AS (
                SELECT service_user_id, MAX(day) AS day
                FROM visit_signals
                WHERE severity = 'high' AND day >= ?
                GROUP BY service_user_id
            ),
            latest_form AS (
                SELECT fs.service_user_id, f.form_name, MAX(f.created_at) AS created_at,
                       MAX(f.id) AS form_id
                FROM forms f JOIN form_subjects fs ON fs.form_id = f.id
                WHERE f.form_name IN ('Care Plan', 'Moving and Handling')
                GROUP BY fs.service_user_id, f.form_name
            )
            SELECT 'service_user', h.service_user_id, h.service_user_id, h.day,
                   'assessment_not_reviewed',
                   lf.form_name || ' F' || lf.form_id || ' last updated ' || lf.created_at
                   || '; high-severity signal on ' || h.day
            FROM latest_high h
            JOIN latest_form lf ON lf.service_user_id = h.service_user_id
            WHERE lf.created_at < h.day
            """,
            (start,),
        )

    counts = dict(
        idx.execute("SELECT issue, COUNT(*) FROM record_issues GROUP BY issue").fetchall()
    )
    return counts
