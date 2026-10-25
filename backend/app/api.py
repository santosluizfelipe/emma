"""HTTP API for the front end.

    uvicorn app.api:app --reload --port 8000

POST /api/chat           {"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
GET  /api/briefing       who needs attention now, straight from the risk tables (no LLM)
GET  /api/records/{ref}  full record behind a citation chip (V205, F11)
GET  /api/health         data freshness and configuration
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import config, db
from app.agent.loop import Agent
from app.agent.tools import ToolContext, get_visit_notes, record_issues, risk_overview, safeguarding_cases
from app.llm import LLMNotConfigured

log = logging.getLogger(__name__)

app = FastAPI(title="Care Data Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_agent: Optional[Agent] = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        try:
            _agent = Agent(org_unit_ids=config.AGENT_ORG_UNIT_IDS)
        except LLMNotConfigured as exc:
            raise HTTPException(503, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(503, str(exc)) from exc
    return _agent


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: List[Turn] = Field(default_factory=list)


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    agent = get_agent()
    result = agent.ask(request.message, [t.model_dump() for t in request.history])
    return {
        "answer": result.answer,
        "citations": result.citations,
        "tool_calls": result.tool_calls,
        "removed_citations": result.removed_citations,
        "unverified_quotes": result.unverified_quotes,
        "tokens": result.tokens,
        "as_of": result.as_of,
    }


@app.get("/api/briefing")
def briefing() -> dict:
    """The start screen: who needs attention this week. Read from the pre-computed risk
    tables with the user's scope, so it is instant, free and the same every time."""
    conn = db.connect_index_readonly()
    try:
        ctx = ToolContext(conn=conn, as_of=db.get_as_of(conn), org_unit_ids=config.AGENT_ORG_UNIT_IDS)
        risk = risk_overview(ctx, days=7, limit=8)
        cases = safeguarding_cases(ctx)
        issues = record_issues(ctx, days=14)
        return {
            "as_of": ctx.as_of,
            "people": [
                {"service_user_id": p["service_user_id"], "name": p["name"], "alert": p["worst_alert_in_period"],
                 "score_now": p["rolling_7d_score_now"], "categories": p["categories_last_7d"],
                 "evidence": p["top_evidence"][:2]}
                for p in risk["people_with_signals"]
            ],
            "people_total": risk["total"],
            "alerting": risk["alerting_in_period"],
            "service_users_in_scope": risk["service_users_in_scope"],
            "safeguarding": {"total": cases["total"], "latest": cases["cases"][:3]},
            "issues": {s["issue"]: s["items"] for s in issues["summary"]},
        }
    finally:
        conn.close()


@app.get("/api/records/{ref}")
def record(ref: str) -> dict:
    ref = ref.upper()
    conn = db.connect_index_readonly()
    try:
        ctx = ToolContext(conn=conn, as_of=db.get_as_of(conn), org_unit_ids=config.AGENT_ORG_UNIT_IDS)
        if ref.startswith("V") and ref[1:].isdigit():
            visits = get_visit_notes(ctx, visit_refs=[ref])["visits"]
            if visits:
                signals = [dict(r) for r in conn.execute(
                    "SELECT category, severity, evidence, escalated FROM visit_signals "
                    "WHERE visit_id = ? AND evidence_verified = 1 ORDER BY severity_score DESC",
                    (int(ref[1:]),),
                )]
                return {"type": "visit", **visits[0], "signals": signals}
        elif ref.startswith("F") and ref[1:].isdigit():
            in_scope, scope_params = ctx.forms_filter("f.id")
            row = conn.execute(
                f"SELECT f.id, f.instance_title, f.form_name, f.created_at FROM forms f WHERE f.id = ? AND {in_scope}",
                (int(ref[1:]), *scope_params),
            ).fetchone()
            if row:
                answers = [dict(a) for a in conn.execute(
                    "SELECT section, question, answer FROM form_answers WHERE form_id = ? ORDER BY position",
                    (row["id"],),
                )]
                return {"type": "form", "ref": ref, "title": row["instance_title"], "form": row["form_name"],
                        "created_at": row["created_at"], "answers": answers}
        raise HTTPException(404, f"record {ref} not found")
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    try:
        conn = db.connect_index_readonly()
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {
            "ok": True,
            "as_of": db.get_as_of(conn),
            "visits": conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM visit_signals").fetchone()[0],
            "extractor": conn.execute("SELECT extractor FROM extraction_runs LIMIT 1").fetchone()[0]
            if conn.execute("SELECT 1 FROM extraction_runs LIMIT 1").fetchone() else None,
            "embedding_model": db.get_meta(conn, "embedding_model"),
            "vector_backend": db.get_meta(conn, "vector_backend"),
            "llm_configured": bool(config.OPENAI_API_KEY),
            "agent_model": config.AGENT_MODEL,
        }
    finally:
        conn.close()
