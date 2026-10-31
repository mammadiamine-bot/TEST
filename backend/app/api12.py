"""AI technician agent routes: sessions, live events, approvals, autonomy policy, maintenance plans, models, local knowledge."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .agent import Agent
from .knowledge import Knowledge
from .security import require_token


class StartIn(BaseModel):
    request: str = Field(min_length=3, max_length=1000)
    target_kind: str = Field(default="worker", max_length=10)
    target: str = Field(max_length=120)
    host: str = Field(default="main", max_length=60)
    model: str | None = Field(default=None, max_length=80)
    lang: str = Field(default="en", max_length=2)


class RepairIn(BaseModel):
    workflow: str = Field(max_length=40)
    finding: str = Field(max_length=40)


class DecideIn(BaseModel):
    confirm: str | None = Field(default=None, max_length=20)


class PolicyIn(BaseModel):
    level: int = Field(ge=0, le=4)
    max_auto_per_hour: int = Field(default=6, ge=0, le=50)


class MonitorIn(BaseModel):
    enabled: bool


class BenchIn(BaseModel):
    host: str = Field(max_length=60)
    model: str = Field(max_length=80)


class PlanIn(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    targets: list[str] = Field(min_length=1, max_length=20)
    workflows: list[str] = Field(min_length=1, max_length=10)
    hours: float = Field(gt=0, le=72)


class FeedbackIn(BaseModel):
    delta: int
    note: str = Field(default="", max_length=200)


class ProcIn(BaseModel):
    key: str = Field(max_length=48)
    title: str = Field(max_length=120)
    signature: list[str] = Field(max_length=8)
    workflow: str | None = Field(default=None, max_length=40)
    body: str = Field(default="", max_length=8000)


class ImportIn(BaseModel):
    bundle: dict
    restore: bool = False


def build_router(core, agent: Agent, kb: Knowledge, ops) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def need_org(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")

    def owner(request: Request) -> None:
        if not request.state.principal.is_owner:
            raise HTTPException(403, "only the owner can do this")

    P = lambda request: request.state.principal  # noqa: E731

    @r.get("/orgs/{oid}/agent/policy")
    def policy(oid: str):
        need_org(oid)
        return agent.policy(oid)

    @r.put("/orgs/{oid}/agent/policy")
    def set_policy(oid: str, b: PolicyIn, request: Request):
        need_org(oid); owner(request)    # the autonomy level is the owner's decision
        return agent.set_policy(oid, b.level, b.max_auto_per_hour, P(request).id)

    @r.get("/orgs/{oid}/agent/monitor")
    def monitor(oid: str):
        need_org(oid)
        return {"enabled": agent.monitor_enabled(oid)}

    @r.put("/orgs/{oid}/agent/monitor")
    def set_monitor(oid: str, b: MonitorIn, request: Request):
        need_org(oid); owner(request)
        return agent.set_monitor(oid, b.enabled, P(request).id)

    @r.post("/agent/resume")
    def resume(request: Request):
        owner(request)
        return agent.resume(P(request).id)

    @r.get("/orgs/{oid}/agent/targets")
    def targets(oid: str, request: Request):
        need_org(oid)
        out = [{"kind": "worker", "id": w["id"], "name": w["name"], "state": core_state(request, w["id"])} for w in db.all("SELECT id,name FROM workers WHERE org_id=?", (oid,))]
        if P(request).is_owner:
            out.insert(0, {"kind": "main", "id": "main", "name": "Main PC", "state": "local"})
        for d in db.all("SELECT host,serial,name FROM android_devices WHERE org_id=?", (oid,)):
            out.append({"kind": "android", "id": f"{d['host']}:{d['serial']}", "name": (d["name"] or d["serial"]) + " (Android)", "state": "assigned"})
        return out

    def core_state(request: Request, wid: str) -> str:
        try:
            return request.app.state.workers.get(wid)["state"]
        except Exception:  # noqa: BLE001
            return "unknown"

    @r.get("/orgs/{oid}/agent/models")
    def models(oid: str):
        need_org(oid)
        return agent.models(oid)

    @r.post("/orgs/{oid}/agent/models/benchmark")
    def bench(oid: str, b: BenchIn, request: Request):
        need_org(oid)
        if b.host != "main" and not db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (b.host, oid)):
            raise HTTPException(422, "that AI host does not belong to this client")
        if P(request).role == "viewer":
            raise HTTPException(403, "your role cannot run benchmarks")
        return agent.benchmark(oid, b.host, b.model)

    @r.post("/orgs/{oid}/agent/sessions", status_code=201)
    def start(oid: str, b: StartIn, request: Request):
        need_org(oid)
        if b.target_kind == "main":
            owner(request)   # the Main PC is the owner's own computer, not a client's
        if b.host != "main" and not db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (b.host, oid)):
            raise HTTPException(422, "that AI host does not belong to this client")
        return agent.start(oid, P(request).id, b.request, b.target_kind, "main" if b.target_kind == "main" else b.target, b.host, b.model, b.lang, P(request).role)

    @r.get("/orgs/{oid}/agent/sessions")
    def sessions(oid: str):
        need_org(oid)
        return agent.list(oid)

    @r.get("/orgs/{oid}/agent/sessions/{sid}")
    def session(oid: str, sid: str, after: int = 0):
        need_org(oid)
        return agent.get(oid, sid, after)

    @r.post("/orgs/{oid}/agent/sessions/{sid}/cancel")
    def cancel(oid: str, sid: str, request: Request):
        need_org(oid)
        return agent.cancel(oid, sid, P(request).id)

    @r.post("/orgs/{oid}/agent/sessions/{sid}/repairs", status_code=201)
    def repair(oid: str, sid: str, b: RepairIn, request: Request):
        need_org(oid)
        return agent.request_repair(oid, sid, b.workflow, b.finding, P(request).id, P(request).role)

    @r.post("/orgs/{oid}/agent/actions/{aid}/approve")
    def approve(oid: str, aid: str, b: DecideIn, request: Request):
        need_org(oid)
        return agent.decide(oid, aid, True, P(request).id, P(request).is_owner, b.confirm)

    @r.post("/orgs/{oid}/agent/actions/{aid}/reject")
    def reject(oid: str, aid: str, request: Request):
        need_org(oid)
        return agent.decide(oid, aid, False, P(request).id, P(request).is_owner, None)

    # ---- maintenance plans (level 4)
    @r.get("/orgs/{oid}/agent/maintenance")
    def plans(oid: str):
        need_org(oid)
        return agent.maintenance_list(oid)

    @r.post("/orgs/{oid}/agent/maintenance", status_code=201)
    def plan_new(oid: str, b: PlanIn, request: Request):
        need_org(oid)
        return agent.maintenance_create(oid, P(request).id, b.title, b.targets, b.workflows, b.hours)

    @r.post("/orgs/{oid}/agent/maintenance/{mid}/approve")
    def plan_ok(oid: str, mid: str, request: Request):
        need_org(oid)
        return agent.maintenance_decide(oid, mid, True, P(request).id, P(request).is_owner)

    @r.post("/orgs/{oid}/agent/maintenance/{mid}/reject")
    def plan_no(oid: str, mid: str, request: Request):
        need_org(oid)
        return agent.maintenance_decide(oid, mid, False, P(request).id, P(request).is_owner)

    # ---- local knowledge
    @r.get("/orgs/{oid}/agent/kb/cases")
    def cases(oid: str, q: str | None = None):
        need_org(oid)
        return kb.similar(oid, q, [], k=20, min_score=0.1) if q else kb.cases(oid)

    @r.get("/orgs/{oid}/agent/kb/cases/{cid}")
    def case(oid: str, cid: str):
        need_org(oid)
        return kb.case(oid, cid)

    @r.post("/orgs/{oid}/agent/kb/cases/{cid}/feedback")
    def feedback(oid: str, cid: str, b: FeedbackIn, request: Request):
        need_org(oid)
        return kb.feedback(oid, cid, b.delta, P(request).id, b.note)

    @r.get("/orgs/{oid}/agent/kb/procedures")
    def procs(oid: str):
        need_org(oid)
        return kb.procedures(oid)

    @r.post("/orgs/{oid}/agent/kb/procedures", status_code=201)
    def proc_new(oid: str, b: ProcIn, request: Request):
        need_org(oid)
        return kb.propose(oid, P(request).id, b.model_dump())

    @r.post("/orgs/{oid}/agent/kb/procedures/{pid}/{act}")
    def proc_act(oid: str, pid: str, act: str, request: Request):
        need_org(oid)
        p = P(request)
        if act == "retire":
            return kb.retire(oid, pid, p.id)
        if act not in ("approve", "reject"):
            raise HTTPException(404, "unknown action")
        return kb.decide(oid, pid, act == "approve", p.id, p.is_owner, ops._same_person)

    @r.get("/orgs/{oid}/agent/kb/export")
    def export(oid: str):
        need_org(oid)
        return kb.export(oid)

    @r.post("/orgs/{oid}/agent/kb/import")
    def import_(oid: str, b: ImportIn, request: Request):
        need_org(oid)
        if b.restore:
            owner(request)   # only the owner can restore approved statuses from a backup
        return kb.import_(oid, P(request).id, b.bundle, b.restore)

    return r
