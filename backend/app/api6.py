"""Jobs, scripts, templates, schedules, inventory. Scoping comes from the /orgs/{id} and /workers/{id} prefixes (tenancy.authorize)."""
from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .ops import INVENTORY_KINDS, NAME, TEMPLATES, Ops
from .security import require_token


class JobIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    targets: list[str] = Field(default_factory=list, max_length=200)
    groups: list[str] = Field(default_factory=list, max_length=20)
    template: str | None = None
    params: dict | None = None
    script_id: str | None = None
    script_version: int | None = None
    shell: str | None = None
    body: str | None = Field(default=None, max_length=20000)
    retries: int = Field(default=0, ge=0, le=5)
    timeout: int = Field(default=300, ge=5, le=3600)


class ApproveIn(BaseModel):
    confirm: str | None = None


class ScriptIn(BaseModel):
    name: str
    shell: str
    body: str = Field(max_length=20000)
    risk: str = "modify"


class VersionIn(BaseModel):
    body: str = Field(max_length=20000)


class ScheduleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    targets: list[str] = Field(min_length=1, max_length=200)
    every_minutes: int
    template: str | None = None
    params: dict | None = None
    shell: str | None = None
    body: str | None = Field(default=None, max_length=20000)


def build_router(core, ops: Ops, entitle: Callable[[str, str], None]) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def actor(request: Request) -> str:
        return request.state.principal.id

    def senior(request: Request) -> bool:
        return request.state.principal.role in ("owner", "administrator")

    def need_org(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")

    @r.get("/ops/templates")
    def templates():
        return [{"id": k, "title": v["title"], "risk": v["risk"], "params": list(v["params"])} for k, v in TEMPLATES.items()]

    @r.post("/orgs/{oid}/jobs", status_code=201)
    def create_job(oid: str, b: JobIn, request: Request):
        need_org(oid)
        entitle(oid, "jobs")
        targets = list(b.targets)
        for gid in b.groups:  # groups expand to the client's own machines only
            if not db.one("SELECT 1 AS x FROM groups WHERE id=? AND org_id=?", (gid, oid)):
                raise HTTPException(422, "unknown group for this client")
            targets += [m["rid"] for m in db.all("SELECT rid FROM group_members WHERE group_id=? AND kind='workers'", (gid,))]
        return ops.create_job(oid, actor(request), targets=targets, title=b.title, shell=b.shell, body=b.body, template=b.template, params=b.params,
                              script_id=b.script_id, script_version=b.script_version, retries=b.retries, timeout=b.timeout)

    @r.get("/orgs/{oid}/jobs")
    def jobs(oid: str, status: str | None = None, limit: int = 100):
        need_org(oid)
        q, a = "SELECT id,title,risk,status,approval,created_by,created_at,shell FROM jobs WHERE org_id=?", [oid]
        if status:
            q += " AND status=?"; a.append(status)
        return db.all(q + " ORDER BY created_at DESC LIMIT ?", tuple(a) + (min(max(limit, 1), 500),))

    @r.get("/orgs/{oid}/jobs/{jid}")
    def job(oid: str, jid: str):
        ops._job_of(oid, jid)
        return ops.job(jid)

    @r.post("/orgs/{oid}/jobs/{jid}/approve")
    def approve(oid: str, jid: str, b: ApproveIn, request: Request):
        return ops.approve(oid, jid, actor(request), request.state.principal.is_owner, b.confirm)

    @r.post("/orgs/{oid}/jobs/{jid}/reject")
    def reject(oid: str, jid: str, request: Request):
        return ops.reject(oid, jid, actor(request))

    @r.post("/orgs/{oid}/jobs/{jid}/cancel")
    def cancel(oid: str, jid: str, request: Request):
        return ops.cancel(oid, jid, actor(request))

    @r.get("/workers/{wid}/runs")
    def device_history(wid: str, limit: int = 100):
        return db.all("SELECT r.id,r.job_id,j.title,j.risk,r.status,r.exit_code,r.started_at,r.finished_at,r.error FROM job_runs r JOIN jobs j ON j.id=r.job_id WHERE r.worker_id=? ORDER BY COALESCE(r.started_at,j.created_at) DESC LIMIT ?", (wid, min(max(limit, 1), 500)))

    @r.post("/workers/{wid}/inventory/{kind}")
    def collect(wid: str, kind: str, request: Request):
        w = db.one("SELECT org_id FROM workers WHERE id=?", (wid,))
        if not w or not w["org_id"]:
            raise HTTPException(409, "assign this machine to a client first")
        return ops.collect(w["org_id"], wid, kind, actor(request))

    @r.get("/workers/{wid}/inventory/{kind}")
    def latest(wid: str, kind: str):
        if kind not in INVENTORY_KINDS:
            raise HTTPException(422, "unknown inventory kind")
        return ops.latest(wid, kind) or {"ts": None, "data": None}

    @r.post("/orgs/{oid}/scripts", status_code=201)
    def mk_script(oid: str, b: ScriptIn, request: Request):
        need_org(oid)
        return ops.create_script(oid, b.name, b.shell, b.body, b.risk, actor(request), senior(request))

    @r.get("/orgs/{oid}/scripts")
    def scripts(oid: str):
        need_org(oid)
        return db.all("SELECT s.*, (SELECT MAX(version) FROM script_versions v WHERE v.script_id=s.id) AS latest FROM scripts s WHERE org_id=? ORDER BY name", (oid,))

    @r.post("/orgs/{oid}/scripts/{sid}/versions", status_code=201)
    def new_version(oid: str, sid: str, b: VersionIn, request: Request):
        return ops.new_version(oid, sid, b.body, actor(request), senior(request))

    @r.get("/orgs/{oid}/scripts/{sid}/versions")
    def versions(oid: str, sid: str):
        if not db.one("SELECT 1 AS x FROM scripts WHERE id=? AND org_id=?", (sid, oid)):
            raise HTTPException(404, "unknown script")
        return db.all("SELECT version,sha256,author,created_at,body FROM script_versions WHERE script_id=? ORDER BY version DESC", (sid,))

    @r.post("/orgs/{oid}/schedules", status_code=201)
    def mk_schedule(oid: str, b: ScheduleIn, request: Request):
        need_org(oid)
        entitle(oid, "scheduler")
        return ops.create_schedule(oid, actor(request), title=b.title, targets=b.targets, every_minutes=b.every_minutes, template=b.template, params=b.params, shell=b.shell, body=b.body)

    @r.get("/orgs/{oid}/schedules")
    def schedules(oid: str):
        need_org(oid)
        return [{**s, "targets": json.loads(s["targets"])} for s in db.all("SELECT * FROM schedules WHERE org_id=?", (oid,))]

    @r.delete("/orgs/{oid}/schedules/{sid}")
    def del_schedule(oid: str, sid: str, request: Request):
        db.execute("DELETE FROM schedules WHERE id=? AND org_id=?", (sid, oid))
        db.audit(actor(request), "schedule.deleted", None, org=oid, schedule=sid)
        return {"ok": True}

    return r
