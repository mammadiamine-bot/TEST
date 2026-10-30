"""Routes for AI Worker Stations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .security import require_token
from .service import CoreError
from .workers import Workers


class WorkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=255)
    fingerprint: str = Field(min_length=64, max_length=100)
    rustdesk_id: str | None = Field(default=None, max_length=32)
    notes: str = Field(default="", max_length=500)


class EnrollIn(BaseModel):
    code: str = Field(min_length=8, max_length=8)
    days: float | None = Field(default=None, gt=0, le=365)


class PatchIn(BaseModel):
    rustdesk_id: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=500)


def build_router(workers: Workers) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    @r.get("/workers")
    def lst():
        return workers.list()

    @r.post("/workers", status_code=201)
    def add(b: WorkerIn):
        return workers.add(b.name, b.address, b.fingerprint, b.rustdesk_id, b.notes)

    @r.get("/workers/{wid}")
    def one(wid: str):
        return workers.get(wid)

    @r.patch("/workers/{wid}")
    def patch(wid: str, b: PatchIn):
        return workers.update(wid, b.rustdesk_id, b.notes)

    @r.post("/workers/{wid}/enroll")
    def enroll(wid: str, b: EnrollIn):
        return workers.enroll(wid, b.code, b.days)

    @r.delete("/workers/{wid}")
    def rm(wid: str, revoke: bool = True):
        workers.remove(wid, revoke); return {"ok": True}

    @r.post("/workers/poll")
    def poll():
        return workers.poll_once()

    @r.api_route("/workers/{wid}/w/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy(wid: str, path: str, request: Request):
        body = None
        if request.method in ("POST", "PUT"):
            raw = await request.body()
            if raw:
                import json
                try:
                    body = json.loads(raw)
                except ValueError:
                    raise HTTPException(400, "invalid JSON")
        qs = request.url.query
        return await run_in_threadpool(workers.call, wid, request.method, "/" + path + (f"?{qs}" if qs else ""), body)

    return r
