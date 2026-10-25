"""Free-form read-only SQL, guarded by app.agent.sql_guard."""

from __future__ import annotations

from app.agent import sql_guard
from app.agent.tools.context import ToolContext

SCHEMA_HINT = """Tables (SQLite):
service_users(id, name, date_of_birth, sex, status, org_unit_id)
carers(id, name, status, org_unit_id)
visits(id, service_user_id, carer_id, start_time, end_time, status, note)  -- start_time 'YYYY-MM-DD HH:MM:SS'
visit_activities(visit_id, position, name, status, description, comment, is_medication, closed_at)
forms(id, form_name, form_type, entity_type, instance_title, org_unit_id, created_at)
form_answers(form_id, position, section, question, answer)
form_subjects(form_id, service_user_id, confidence, method)  -- who each form is about
visit_signals(id, visit_id, service_user_id, observed_at, day, category, severity, severity_score, evidence, escalated, matches_care_plan_risk, contradicts_status, evidence_verified)
su_daily_risk(service_user_id, day, score, rolling_7d, categories, top_evidence, alert)
record_issues(record_type, record_id, service_user_id, day, issue, detail)
Note: visit/activity status is always 'Completed' in this data and visit times are scheduled, so counting them says little about wellbeing."""


def query_sql(ctx: ToolContext, sql: str) -> dict:
    """Read-only SQL over curated tables for counts, rotas and dates, limited to the user's org units."""
    return sql_guard.execute(ctx.conn, sql, org_unit_ids=ctx.org_unit_ids)


SPECS = {
    "query_sql": {
        "fn": query_sql,
        "description": "Read-only SELECT for counts, lists, rotas and dates. One statement; max 200 rows.\n" + SCHEMA_HINT,
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    },
}
