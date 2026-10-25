"""Reading source records: visit notes, care plans, safeguarding enquiries."""

from __future__ import annotations

from typing import List, Optional

from app.agent.tools.context import OPT_DATE, ToolContext, page_info, person_name, require_person


def get_visit_notes(ctx: ToolContext, service_user_id: Optional[int] = None,
                    visit_refs: Optional[List[str]] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, limit: int = 10) -> dict:
    """Full visit records (note + tasks). Either specific refs, or one person's recent visits."""
    limit = max(1, min(limit, 20))
    if visit_refs:
        ids = [int(r.strip().upper().lstrip("V")) for r in visit_refs if r.strip().upper().lstrip("V").isdigit()][:20]
        if not ids:
            raise ValueError("visit_refs must look like ['V205']")
        in_scope, scope_params = ctx.people_filter("v.service_user_id")
        where, params = f"v.id IN ({','.join('?' * len(ids))}) AND {in_scope}", ids + scope_params
    elif service_user_id is not None:
        require_person(ctx, service_user_id)
        where = "v.service_user_id = ? AND v.start_time >= ? AND v.start_time < date(?, '+1 day')"
        params = [service_user_id, date_from or ctx.days_ago(7), date_to or ctx.today]
    else:
        raise ValueError("give service_user_id or visit_refs")
    rows = ctx.conn.execute(
        f"""
        SELECT v.id, v.service_user_id, su.name AS service_user, c.name AS carer,
               v.start_time, v.end_time, v.status, v.note, COUNT(*) OVER () AS n
        FROM visits v JOIN service_users su ON su.id = v.service_user_id
        LEFT JOIN carers c ON c.id = v.carer_id
        WHERE {where} ORDER BY v.start_time DESC, v.id DESC LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    visits = []
    for v in rows:
        tasks = ctx.conn.execute(
            "SELECT name, status, comment FROM visit_activities WHERE visit_id = ? ORDER BY position",
            (v["id"],),
        ).fetchall()
        visits.append({
            "ref": f"V{v['id']}", "service_user": v["service_user"], "carer": v["carer"],
            "start": v["start_time"], "end": v["end_time"], "status": v["status"], "note": v["note"],
            "tasks": [dict(t) for t in tasks],
        })
    return {"visits": visits, **page_info(rows[0]["n"] if rows else 0, len(visits)), "note": "newest first"}


def get_care_plan(ctx: ToolContext, service_user_id: int, sections: Optional[List[str]] = None) -> dict:
    """Care plan and moving & handling assessment: baseline, conditions, known risks, controls."""
    name = require_person(ctx, service_user_id)
    in_scope, scope_params = ctx.forms_filter("f.id")
    forms = ctx.conn.execute(
        f"""
        SELECT f.id, f.form_name, f.instance_title, f.created_at
        FROM forms f JOIN form_subjects fs ON fs.form_id = f.id
        WHERE fs.service_user_id = ? AND f.form_type IN ('Care Plans', 'Risk Assessments') AND {in_scope}
        ORDER BY f.created_at DESC, f.id DESC LIMIT 5
        """,
        (service_user_id, *scope_params),
    ).fetchall()
    wanted = [s.lower() for s in (sections or []) if s]
    out = []
    for f in forms:
        answers = []
        for a in ctx.conn.execute(
            "SELECT section, question, answer FROM form_answers WHERE form_id = ? ORDER BY position", (f["id"],)
        ):
            label = f"{a['section']} {a['question']}".lower()
            if not wanted or any(w in label for w in wanted):
                answers.append(dict(a))
        out.append({"ref": f"F{f['id']}", "form": f["form_name"], "title": f["instance_title"],
                    "created_at": f["created_at"], "answers": answers})
    return {"service_user": {"id": service_user_id, "name": name}, "forms": out}


def safeguarding_cases(ctx: ToolContext, service_user_id: Optional[int] = None) -> dict:
    """Safeguarding enquiries with their linked person, status and outstanding actions."""
    in_scope, params = ctx.forms_filter("f.id")
    sql = f"""
        SELECT f.id, f.instance_title, f.created_at, fs.service_user_id, fs.method, fs.confidence,
               COUNT(*) OVER () AS n
        FROM forms f LEFT JOIN form_subjects fs ON fs.form_id = f.id
        WHERE f.form_type = 'Safeguarding/Complaints' AND {in_scope}
    """
    if service_user_id is not None:
        require_person(ctx, service_user_id)
        sql += " AND fs.service_user_id = ?"
        params.append(service_user_id)
    sql += " ORDER BY f.created_at DESC, f.id DESC LIMIT 25"
    rows = ctx.conn.execute(sql, params).fetchall()
    cases = []
    for f in rows:
        answers = {a["question"]: a["answer"] for a in ctx.conn.execute(
            "SELECT question, answer FROM form_answers WHERE form_id = ?", (f["id"],))}
        cases.append({
            "ref": f"F{f['id']}",
            "reference": answers.get("Reference"),
            "service_user": person_name(ctx, f["service_user_id"]),
            "service_user_id": f["service_user_id"],
            "linked_by": f["method"],
            "category": answers.get("Category"),
            "date_raised": answers.get("Date raised") or f["created_at"],
            "observed": answers.get("What was observed or disclosed"),
            "immediate_action": answers.get("Immediate action taken"),
            "referrals": answers.get("Referrals made"),
            "current_status": answers.get("Current status"),
        })
    return {"cases": cases, **page_info(rows[0]["n"] if rows else 0, len(cases)), "note": "newest first"}


SPECS = {
    "get_visit_notes": {
        "fn": get_visit_notes,
        "description": "Full visit records (note, carer, tasks and comments). Pass visit_refs like ['V205'] to read specific visits, or service_user_id for their recent visits (default last 7 days).",
        "parameters": {"type": "object", "properties": {
            "service_user_id": {"type": "integer"},
            "visit_refs": {"type": "array", "items": {"type": "string"}},
            "date_from": OPT_DATE, "date_to": OPT_DATE,
            "limit": {"type": "integer", "description": "max visits, default 10, max 20"}}},
    },
    "get_care_plan": {
        "fn": get_care_plan,
        "description": "A person's care plan and moving & handling assessment: conditions, medication, mobility, known risks, control measures. Optionally filter by section/question keywords, e.g. ['risk', 'mobility'].",
        "parameters": {"type": "object", "properties": {
            "service_user_id": {"type": "integer"},
            "sections": {"type": "array", "items": {"type": "string"}}},
            "required": ["service_user_id"]},
    },
    "safeguarding_cases": {
        "fn": safeguarding_cases,
        "description": "Safeguarding enquiries: who, category, what was observed, actions, referrals, current status.",
        "parameters": {"type": "object", "properties": {"service_user_id": {"type": "integer"}}},
    },
}
