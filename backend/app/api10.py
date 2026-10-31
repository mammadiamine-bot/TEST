"""Android devices (ADB) for one client: discovery, live screen, input, diagnostics, and approval-gated changes."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .android import Android
from .security import require_token


class AssignIn(BaseModel):
    serial: str = Field(min_length=4, max_length=64)
    name: str = Field(default="", max_length=80)


class ShellIn(BaseModel):
    cmd: str = Field(min_length=1, max_length=300)


class AppIn(BaseModel):
    action: str
    package: str


class ReqIn(BaseModel):
    kind: str
    arg: str = Field(default="", max_length=300)
    apk_b64: str | None = None


class DecideIn(BaseModel):
    confirm: str | None = None


def build_router(core, android: Android, ops) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def guard(oid: str, host: str, serial: str | None = None) -> None:
        if host == "main":
            if serial and not db.one("SELECT 1 AS x FROM android_devices WHERE host='main' AND serial=? AND org_id=?", (serial, oid)):
                raise HTTPException(403, "not permitted")   # a phone on the Main PC is visible to a client only after the owner assigned it
        elif not db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (host, oid)):
            raise HTTPException(403, "not permitted")

    @r.get("/orgs/{oid}/android/devices")
    def devices(oid: str):
        mine = db.all("SELECT serial,name FROM android_devices WHERE host='main' AND org_id=?", (oid,))
        hosts = ([("main", "Main PC")] if mine else []) + [(w["id"], w["name"]) for w in db.all("SELECT id,name FROM workers WHERE org_id=?", (oid,))]
        res = []
        for h, hn in hosts:
            try:
                found = {d["serial"]: d for d in android.devices(oid, h)}
            except Exception as e:  # noqa: BLE001 - one unreachable computer must not hide the others
                res.append({"host": h, "host_name": hn, "error": str(getattr(e, "detail", e))[:200]}); continue
            if h == "main":
                for o in mine:
                    d = found.get(o["serial"])
                    res.append({"serial": o["serial"], "state": "not_connected", **(d or {}), "host": "main", "host_name": hn, "name": o["name"]})
            else:
                res += [{**d, "host_name": hn, "name": d.get("model") or d["serial"]} for d in found.values()]
        return res

    @r.get("/android/unassigned")
    def unassigned():
        found = android.devices("", "main")
        have = {a["serial"] for a in db.all("SELECT serial FROM android_devices WHERE host='main'")}
        return [d for d in found if d["serial"] not in have]

    @r.put("/orgs/{oid}/android/assign")
    def assign(oid: str, b: AssignIn, request: Request):
        if not request.state.principal.is_owner:
            raise HTTPException(403, "not permitted")   # phones plugged into the owner's own PC are assigned by the owner only
        db.execute("INSERT OR REPLACE INTO android_devices(host,serial,org_id,name) VALUES('main',?,?,?)", (b.serial, oid, b.name or b.serial))
        db.audit(request.state.principal.id, "android.assigned", None, org=oid, serial=b.serial)
        return {"ok": True}

    @r.get("/orgs/{oid}/android/{host}/{serial}/info")
    def info(oid: str, host: str, serial: str):
        guard(oid, host, serial); return android.info(oid, host, serial)

    @r.get("/orgs/{oid}/android/{host}/{serial}/screen")
    def screen(oid: str, host: str, serial: str, w: int = 540):
        guard(oid, host, serial); return Response(android.screenshot(oid, host, serial, w), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @r.post("/orgs/{oid}/android/{host}/{serial}/input")
    def inp(oid: str, host: str, serial: str, ev: dict):
        guard(oid, host, serial); return android.input(oid, host, serial, ev)

    @r.post("/orgs/{oid}/android/{host}/{serial}/shell")
    def shell(oid: str, host: str, serial: str, b: ShellIn, request: Request):
        guard(oid, host, serial)
        out = android.shell_readonly(oid, host, serial, b.cmd)
        db.audit(request.state.principal.id, "android.shell", None, org=oid, host=host, serial=serial, cmd=b.cmd[:120])
        return out

    @r.get("/orgs/{oid}/android/{host}/{serial}/logcat")
    def logcat(oid: str, host: str, serial: str, lines: int = 300, level: str = "W"):
        guard(oid, host, serial); return android.logcat(oid, host, serial, lines, level)

    @r.get("/orgs/{oid}/android/{host}/{serial}/packages")
    def packages(oid: str, host: str, serial: str, system: bool = False):
        guard(oid, host, serial); return android.packages(oid, host, serial, system)

    @r.post("/orgs/{oid}/android/{host}/{serial}/app")
    def app(oid: str, host: str, serial: str, b: AppIn, request: Request):
        guard(oid, host, serial)
        res = android.app(oid, host, serial, b.action, b.package)
        db.audit(request.state.principal.id, "android.app", None, org=oid, serial=serial, app_action=b.action, package=b.package)
        return res

    @r.post("/orgs/{oid}/android/{host}/{serial}/requests", status_code=201)
    def request_change(oid: str, host: str, serial: str, b: ReqIn, request: Request):
        guard(oid, host, serial)
        apk = base64.b64decode(b.apk_b64) if b.apk_b64 else None
        return android.request(oid, host, serial, b.kind, b.arg, request.state.principal.id, apk)

    @r.get("/orgs/{oid}/android/requests")
    def requests(oid: str):
        return db.all("SELECT * FROM android_actions WHERE org_id=? ORDER BY created_at DESC LIMIT 100", (oid,))

    def decide(oid: str, aid: str, b: DecideIn, request: Request, ok: bool):
        p = request.state.principal
        return android.decide(oid, aid, p.id, ok, p.is_owner, ops._same_person, b.confirm)

    @r.post("/orgs/{oid}/android/requests/{aid}/approve")
    def approve(oid: str, aid: str, b: DecideIn, request: Request):
        return decide(oid, aid, b, request, True)

    @r.post("/orgs/{oid}/android/requests/{aid}/reject")
    def reject(oid: str, aid: str, request: Request):
        return decide(oid, aid, DecideIn(), request, False)

    return r
