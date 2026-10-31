from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import ai
from . import tools as toolmod
from .config import Settings
from .security import require_token
from .service import Core, CoreError
from .targetcore import TargetError

NAME = Field(min_length=1, max_length=120)


class DeviceIn(BaseModel):
    name: str = NAME
    address: str = Field(min_length=1, max_length=255)
    transport: str
    owner_name: str = NAME
    owner_contact: str = Field(min_length=3, max_length=200)
    authorization_note: str = Field(min_length=10, max_length=1000, description="How the owner authorized this support")
    scenario: str | None = None
    kind: str = "workstation"
    allow_public: bool = False


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)  # 6 digits: controller-issued; 8 digits: shown by the agent on the client's screen
    fingerprint: str = Field(default="", max_length=200)
    host_key: str | None = Field(default=None, max_length=2000)


class SessionIn(BaseModel):
    scope: str
    minutes: int = Field(ge=1, le=240)


class ConsentIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    person: str = NAME


class DiagIn(BaseModel):
    modules: list[str] | None = None


class PlanIn(BaseModel):
    run_id: str


class ApproveIn(BaseModel):
    approver: str = NAME
    confirm_high_risk: bool = False


class ConsoleIn(BaseModel):
    command: str = Field(max_length=200)


class NoteIn(BaseModel):
    author: str = NAME
    body: str = Field(min_length=1, max_length=4000)


class ChatIn(BaseModel):
    device_id: str
    message: str = Field(min_length=1, max_length=2000)


def create_app(settings: Settings | None = None, core: Core | None = None) -> FastAPI:
    settings = settings or Settings()
    core = core or Core(settings)
    app = FastAPI(title="Claude IT Technician API", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.settings, app.state.core = settings, core
    from .api2 import build_router
    from .dev import Dev
    from .tasks import Tasks
    dev = Dev(core); tasks = Tasks(core, dev)
    from .api3 import build_router as build_workers_router
    from .workers import Workers
    workers = Workers(core)
    app.state.dev, app.state.tasks, app.state.workers = dev, tasks, workers

    app.add_middleware(CORSMiddleware, allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost", "http://localhost:1420", "http://127.0.0.1:1420"],
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(CoreError)
    async def _core_err(_: Request, e: CoreError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(TargetError)
    async def _target_err(_: Request, e: TargetError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(toolmod.ToolError)
    async def _tool_err(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ready")
    def ready():
        """Readiness for orchestrators: the database answers and the audit chain verifies. No tenant data."""
        try:
            return {"ready": bool(core.db.one("SELECT 1 AS x")) and core.db.verify_audit_chain()}
        except Exception:  # noqa: BLE001
            raise HTTPException(503, "not ready")

    r = APIRouter(dependencies=[Depends(require_token)])

    @r.get("/status")
    def status():
        cloud = core.cloud_probe()
        return {"cloud_available": cloud, "claude_enabled": settings.claude_enabled and cloud,
                "mode": "online" if cloud else "offline", "audit_chain_ok": core.db.verify_audit_chain(),
                "offline_queue": core.db.one("SELECT COUNT(*) n FROM offline_queue WHERE reviewed=0")["n"]}

    @r.get("/tools")
    def list_tools():
        return [{"id": t.id, "title": t.title, "risk": t.risk.value, "kind": t.kind, "timeout_s": t.timeout_s,
                 "params": list(t.params)} for t in toolmod.REGISTRY.values()]

    @r.get("/devices")
    def devices():
        core.ensure_local_device()
        return core.devices()

    @r.post("/devices", status_code=201)
    def create_device(b: DeviceIn):
        return core.create_device(b.name, b.address, b.transport, b.owner_name, b.owner_contact, b.authorization_note, b.scenario,
                                  kind=b.kind, allow_public=b.allow_public)

    @r.get("/devices/{did}")
    def device(did: str):
        return core.device(did)

    @r.post("/devices/{did}/pair")
    def pair(did: str, b: PairIn):
        return core.pair(did, b.code, b.fingerprint, b.host_key)

    @r.post("/devices/{did}/revoke")
    def revoke(did: str):
        return core.revoke_device(did)

    @r.get("/devices/{did}/sessions")
    def sessions(did: str):
        return core.sessions(did)

    @r.post("/devices/{did}/sessions", status_code=201)
    def req_session(did: str, b: SessionIn):
        return core.request_session(did, b.scope, b.minutes)

    @r.post("/sessions/{sid}/consent")
    def consent(sid: str, b: ConsentIn):
        return core.consent(sid, b.code, b.person)

    @r.post("/sessions/{sid}/revoke")
    def end(sid: str):
        return core.end_session(sid, "technician")

    @r.get("/devices/{did}/metrics")
    def metrics(did: str):
        return core.metrics(did)

    @r.post("/devices/{did}/diagnose")
    def diagnose(did: str, b: DiagIn):
        return core.diagnose(did, b.modules)

    @r.get("/devices/{did}/runs")
    def runs(did: str):
        return core.runs(did)

    @r.get("/runs/{rid}")
    def run(rid: str):
        return core.run(rid)

    @r.post("/devices/{did}/console")
    def console(did: str, b: ConsoleIn):
        return core.console(did, b.command)

    @r.post("/devices/{did}/plans", status_code=201)
    def mk_plan(did: str, b: PlanIn):
        return core.create_plan(did, b.run_id)

    @r.get("/devices/{did}/plans")
    def plans(did: str):
        return core.plans(did)

    @r.get("/plans/{pid}")
    def plan(pid: str):
        return core.plan(pid)

    @r.post("/plans/{pid}/steps/{sid}/approve")
    def approve(pid: str, sid: str, b: ApproveIn):
        return core.approve_step(pid, sid, b.approver, b.confirm_high_risk)

    @r.post("/plans/{pid}/approve-low-risk")
    def approve_low(pid: str, b: ApproveIn):
        return core.approve_low_risk(pid, b.approver)

    @r.post("/plans/{pid}/steps/{sid}/reject")
    def reject(pid: str, sid: str):
        return core.reject_step(pid, sid, "technician")

    @r.post("/plans/{pid}/execute")
    def execute(pid: str):
        return core.execute_plan(pid)

    @r.get("/devices/{did}/notes")
    def notes(did: str):
        return core.notes(did)

    @r.post("/devices/{did}/notes", status_code=201)
    def add_note(did: str, b: NoteIn):
        return core.add_note(did, b.author, b.body)

    @r.get("/audit")
    def audit(device_id: str | None = None):
        return core.audit_log(device_id)

    @r.post("/chat")
    def chat(b: ChatIn):
        d = core.device(b.device_id)
        runs_ = core.runs(d["id"])
        findings = core.run(runs_[0]["id"])["findings"] if runs_ else []
        cloud = core.cloud_probe()
        if cloud and settings.claude_enabled:
            try:
                return {"source": "claude", "cloud_available": True, "text": ai.claude_answer(b.message, findings)}
            except Exception as e:  # noqa: BLE001 - any SDK/network failure falls back, visibly
                return {"source": "scripted", "cloud_available": True, "fallback_reason": f"Claude failed: {type(e).__name__}",
                        "text": ai.scripted_answer(b.message, findings)}
        if not cloud:
            from .db import new_id
            import time
            summary = [{"id": f["id"], "severity": f["severity"]} for f in findings]  # no evidence/hostnames/IPs
            core.db.execute("INSERT INTO offline_queue(id,created_at,payload) VALUES(?,?,?)",
                            (new_id("q"), time.time(), json.dumps({"question": ai.redact(b.message), "findings": summary})))
        return {"source": "scripted", "cloud_available": cloud, "text": ai.scripted_answer(b.message, findings),
                "notice": "Cloud Claude AI is unavailable; this is a deterministic scripted summary." if not cloud else "Claude is disabled in settings."}

    @r.get("/devices/{did}/report")
    def report(did: str, run_id: str | None = None, plan_id: str | None = None):
        from . import reports
        d = core.device(did)
        plan_ = core.plan(plan_id) if plan_id else None
        rid = run_id or (plan_["run_id"] if plan_ else None) or (core.runs(did)[0]["id"] if core.runs(did) else None)
        if not rid:
            raise HTTPException(404, "no diagnostic run to report on")
        data = reports.build_data(d, core.run(rid), plan_, core.notes(did))
        path = reports.write_pdf(data, settings.reports_dir / f"{did}-{rid}.pdf")
        core.db.audit("technician", "report.generated", did, file=path.name)
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    app.include_router(r)
    app.include_router(build_router(core, dev, tasks))
    app.include_router(build_workers_router(workers))
    from .api4 import build_router as build_tenancy_router
    rmm_router = build_tenancy_router(core, workers)
    app.include_router(rmm_router)
    app.include_router(rmm_router.public)
    from .api5 import build_router as build_monitor_router
    from .monitor import Monitor
    monitor = Monitor(core.db)
    workers.listeners.append(monitor.on_poll)
    app.state.monitor = monitor
    app.include_router(build_monitor_router(core, monitor))
    from .api6 import build_router as build_ops_router
    from .ops import Ops
    ops = Ops(core.db, workers.call)
    app.state.ops = ops
    from .api7 import build_router as build_billing_router
    from .billing import Billing
    core.billing = Billing(core.db, settings.data_dir)
    app.include_router(build_ops_router(core, ops, core.billing.entitle))  # fails closed: no plan/feature, no job
    app.include_router(build_billing_router(core, core.billing))
    from .aitech import AiTech
    from .api8 import build_router as build_ai_router
    app.state.ai = AiTech(core.db, ops, core.billing)
    app.include_router(build_ai_router(core, app.state.ai))
    return app
