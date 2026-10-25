"""Risk: who needs attention, what changed for one person, documentation issues."""

from __future__ import annotations

import json
from typing import List, Optional

from app import config
from app.agent.tools.context import OPT_DATE, ToolContext, person_name, require_person
from app.ingest.signals import CATEGORIES


def risk_overview(ctx: ToolContext, days: int = 7, limit: int = 10) -> dict:
    """Service users ranked by recent risk, with the evidence behind each."""
    days = max(1, min(days, 31))
    limit = max(1, min(limit, 25))
    since = ctx.days_ago(days)
    evidence_from = ctx.days_ago(days + config.ROLLING_DAYS - 1)
    rows = ctx.conn.execute(
        """
        WITH window AS (
            SELECT service_user_id,
                   MAX(CASE alert WHEN 'immediate' THEN 2 WHEN 'trend' THEN 1 ELSE 0 END) AS worst_alert,
                   MAX(rolling_7d) AS peak_rolling_7d
            FROM su_daily_risk WHERE day BETWEEN ? AND ?
            GROUP BY service_user_id
        )
        SELECT w.service_user_id, su.name, w.worst_alert, w.peak_rolling_7d,
               r.rolling_7d AS rolling_7d_now, r.alert AS alert_now, r.categories
        FROM window w
        JOIN service_users su ON su.id = w.service_user_id
        LEFT JOIN su_daily_risk r ON r.service_user_id = w.service_user_id AND r.day = ?
        WHERE w.worst_alert > 0 OR w.peak_rolling_7d > 0
        ORDER BY w.worst_alert DESC, rolling_7d_now DESC, w.peak_rolling_7d DESC
        LIMIT ?
        """,
        (since, ctx.today, ctx.today, limit),
    ).fetchall()

    people = []
    for r in rows:
        signals = ctx.conn.execute(
            """
            SELECT 'V' || visit_id AS ref, day, category, severity, evidence, escalated
            FROM visit_signals
            WHERE service_user_id = ? AND day BETWEEN ? AND ? AND evidence_verified = 1
            ORDER BY severity_score DESC, day DESC
            LIMIT 6
            """,
            # rolling scores in the period include the 6 days before it, so does the evidence
            (r["service_user_id"], evidence_from, ctx.today),
        ).fetchall()
        people.append({
            "service_user_id": r["service_user_id"],
            "name": r["name"],
            "worst_alert_in_period": ["none", "trend", "immediate"][r["worst_alert"]],
            "rolling_7d_score_now": r["rolling_7d_now"],
            "peak_rolling_7d_score": r["peak_rolling_7d"],
            "categories_last_7d": json.loads(r["categories"] or "[]"),
            "top_evidence": [dict(s) for s in signals],
        })
    total = ctx.conn.execute(
        "SELECT COUNT(DISTINCT service_user_id) FROM su_daily_risk WHERE day = ?", (ctx.today,)
    ).fetchone()[0]
    return {
        "period": {"from": since, "to": ctx.today},
        "scoring": f"severity weights low=1 medium=2 high=3; trend alert when 7-day score >= {config.TREND_ALERT_THRESHOLD}; immediate alert on any high-severity signal",
        "service_users_in_scope": total,
        "people_with_signals": people,
        "people_with_no_signals": total - len(people) if len(people) < limit else "not computed (limit reached)",
    }


def signal_timeline(ctx: ToolContext, service_user_id: int, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, categories: Optional[List[str]] = None) -> dict:
    """What changed for one person, in date order, with verbatim quotes."""
    name = require_person(ctx, service_user_id)
    date_from = date_from or ctx.days_ago(14)
    date_to = date_to or ctx.today
    sql = (
        "SELECT 'V' || visit_id AS ref, observed_at, category, severity, evidence, escalated, "
        "matches_care_plan_risk, contradicts_status FROM visit_signals "
        "WHERE service_user_id = ? AND day BETWEEN ? AND ? AND evidence_verified = 1"
    )
    params: list = [service_user_id, date_from, date_to]
    if categories:
        cats = [c for c in categories if c in CATEGORIES]
        if cats:
            sql += f" AND category IN ({','.join('?' * len(cats))})"
            params += cats
    sql += " ORDER BY observed_at LIMIT 50"
    signals = [dict(r) for r in ctx.conn.execute(sql, params)]
    visits = ctx.conn.execute(
        "SELECT COUNT(*) FROM visits WHERE service_user_id = ? AND start_time >= ? AND start_time < date(?, '+1 day')",
        (service_user_id, date_from, date_to),
    ).fetchone()[0]
    daily = [dict(r) for r in ctx.conn.execute(
        "SELECT day, score, rolling_7d, alert FROM su_daily_risk "
        "WHERE service_user_id = ? AND day BETWEEN ? AND ? ORDER BY day",
        (service_user_id, date_from, date_to),
    )]
    return {
        "service_user": {"id": service_user_id, "name": name},
        "period": {"from": date_from, "to": date_to},
        "visits_in_period": visits,
        "visits_with_signals": len({s["ref"] for s in signals}),
        "signals": signals,
        "daily_risk": [d for d in daily if d["score"] or d["rolling_7d"] or d["alert"] != "none"],
    }


def record_issues(ctx: ToolContext, service_user_id: Optional[int] = None, issue: Optional[str] = None,
                  days: int = 14) -> dict:
    """Documentation gaps and inconsistencies found by deterministic rules."""
    since = ctx.days_ago(max(1, min(days, 90)))
    where, params = ["day >= ?"], [since]
    if service_user_id is not None:
        where.append("service_user_id = ?")
        params.append(service_user_id)
    if issue:
        where.append("issue = ?")
        params.append(issue)
    clause = " AND ".join(where)
    summary = [dict(r) for r in ctx.conn.execute(
        f"SELECT issue, COUNT(*) AS items, COUNT(DISTINCT record_id) AS records, "
        f"COUNT(DISTINCT service_user_id) AS service_users FROM record_issues WHERE {clause} GROUP BY issue",
        params,
    )]
    items = []
    for r in ctx.conn.execute(
        f"SELECT record_type, record_id, service_user_id, day, issue, detail FROM record_issues "
        f"WHERE {clause} ORDER BY day DESC LIMIT 40", params
    ):
        item = dict(r)
        if r["record_type"] == "visit":
            item["ref"] = f"V{r['record_id']}"
        item["service_user"] = person_name(ctx, r["service_user_id"])
        items.append(item)
    return {
        "since": since,
        "issue_types": {
            "completed_without_comment": "task marked Completed with no comment (one item per task)",
            "status_contradiction": "record says medication not given but task marked Completed",
            "assessment_not_reviewed": "high-severity signal after the latest care plan / risk assessment",
            "unverified_evidence": "an extracted quote could not be found in the source record",
        },
        "summary": summary,
        "items": items,
    }


SPECS = {
    "risk_overview": {
        "fn": risk_overview,
        "description": "Who needs attention: service users ranked by recent risk alerts and scores, each with its top verbatim evidence. Start here for 'who is at risk / who should I worry about'.",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "look-back window, default 7"},
            "limit": {"type": "integer", "description": "max people, default 10"}}},
    },
    "signal_timeline": {
        "fn": signal_timeline,
        "description": "One person's extracted signals in date order (category, severity, quote, escalated) plus daily risk scores. Use to see trends and what changed.",
        "parameters": {"type": "object", "properties": {
            "service_user_id": {"type": "integer"},
            "date_from": OPT_DATE, "date_to": OPT_DATE,
            "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}}},
            "required": ["service_user_id"]},
    },
    "record_issues": {
        "fn": record_issues,
        "description": "Documentation gaps and inconsistencies: tasks completed without comment, status contradicting the comment, assessments not reviewed after a serious event.",
        "parameters": {"type": "object", "properties": {
            "service_user_id": {"type": "integer"},
            "issue": {"type": "string", "enum": ["completed_without_comment", "status_contradiction", "assessment_not_reviewed", "unverified_evidence"]},
            "days": {"type": "integer", "description": "look-back window, default 14"}}},
    },
}
