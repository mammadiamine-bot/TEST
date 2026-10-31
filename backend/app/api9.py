"""Live remote-control sessions (RustDesk viewer) for one computer. Scoping comes from the /workers/{id} prefix (tenancy.authorize)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .remote import Remote
from .security import require_token


class StartIn(BaseModel):
    minutes: int = Field(default=30, ge=1, le=240)


def build_router(core, remote: Remote) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    def org_of(wid: str) -> str:
        w = core.db.one("SELECT org_id FROM workers WHERE id=?", (wid,))
        if not w or not w["org_id"]:
            raise HTTPException(409, "assign this computer to a client first")
        return w["org_id"]

    @r.post("/workers/{wid}/remote/sessions", status_code=201)
    def start(wid: str, b: StartIn, request: Request):
        remote.sweep()
        return remote.start(org_of(wid), wid, request.state.principal.id, b.minutes)

    @r.get("/workers/{wid}/remote/sessions")
    def sessions(wid: str):
        remote.sweep()
        return remote.list(wid)

    @r.post("/workers/{wid}/remote/sessions/{sid}/end")
    def end(wid: str, sid: str, request: Request):
        s = remote.get(sid)
        if not s or s["worker_id"] != wid:
            raise HTTPException(404, "unknown session")
        return remote.end(sid, request.state.principal.id)

    @r.get("/remote/status")
    def status():
        from .remote import rustdesk_exe
        return {"engine": "rustdesk", "installed": bool(rustdesk_exe()), "embedded_in_browser": False}

    return r
