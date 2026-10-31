"""AI Technician and per-client settings routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .aitech import AiTech
from .security import require_token


class AskIn(BaseModel):
    worker_id: str
    request: str = Field(min_length=3, max_length=1000)
    lang: str = Field(default="en", max_length=2)


class SettingsIn(BaseModel):
    ai_enabled: bool = True
    ai_cloud: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)


def build_router(core, ai: AiTech) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def need_org(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")

    @r.get("/ai/status")
    def status():
        return ai.status()

    @r.post("/orgs/{oid}/ai/ask", status_code=201)
    def ask(oid: str, b: AskIn, request: Request):
        need_org(oid)
        return ai.ask(oid, b.worker_id, request.state.principal.id, b.request, b.lang)

    @r.get("/orgs/{oid}/ai/runs")
    def runs(oid: str):
        need_org(oid)
        return db.all("SELECT id,worker_id,actor,request,engine,model,tokens_in,tokens_out,status,ts FROM ai_runs WHERE org_id=? ORDER BY ts DESC LIMIT 200", (oid,))

    @r.get("/orgs/{oid}/ai/runs/{rid}")
    def run(oid: str, rid: str):
        row = db.one("SELECT id FROM ai_runs WHERE id=? AND org_id=?", (rid, oid))
        if not row:
            raise HTTPException(404, "unknown AI run")
        return ai.get(rid)

    @r.post("/orgs/{oid}/ai/runs/{rid}/proposals/{n}/submit", status_code=201)
    def submit(oid: str, rid: str, n: int, request: Request):
        return ai.propose(oid, rid, n, request.state.principal.id)

    @r.get("/orgs/{oid}/settings")
    def get_settings(oid: str):
        need_org(oid)
        return ai.settings(oid)

    @r.put("/orgs/{oid}/settings")
    def put_settings(oid: str, b: SettingsIn, request: Request):
        need_org(oid)
        db.execute("INSERT OR REPLACE INTO org_settings(org_id,ai_enabled,ai_cloud,retention_days) VALUES(?,?,?,?)", (oid, int(b.ai_enabled), int(b.ai_cloud), b.retention_days))
        db.audit(request.state.principal.id, "settings.changed", None, org=oid, ai_enabled=b.ai_enabled, ai_cloud=b.ai_cloud)
        return ai.settings(oid)

    return r
