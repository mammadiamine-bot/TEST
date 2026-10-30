"""Clients (organizations), sites, assignment of devices/workers, and scoped principals. Access rules live in tenancy.py."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .db import new_id
from .security import require_token
from .netpolicy import classify_path
from .tenancy import mint_principal


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=500)


class AssignIn(BaseModel):
    org_id: str
    site_id: str | None = None


class PrincipalIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str
    org_ids: list[str]


def build_router(core, workers) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def actor(request: Request) -> str:
        return request.state.principal.id

    def need_org(oid: str) -> dict:
        o = db.one("SELECT * FROM orgs WHERE id=?", (oid,))
        if not o:
            raise HTTPException(404, "unknown organization")
        return o

    @r.post("/orgs", status_code=201)
    def create_org(b: OrgIn, request: Request):
        oid = new_id("org")
        db.execute("INSERT INTO orgs(id,name,created_at) VALUES(?,?,?)", (oid, b.name, time.time()))
        db.audit(actor(request), "org.created", None, org=oid, name=b.name)
        return {"id": oid, "name": b.name}

    @r.get("/orgs")
    def list_orgs(request: Request):
        p = request.state.principal
        return [o for o in db.all("SELECT * FROM orgs ORDER BY created_at") if p.is_owner or o["id"] in p.orgs]

    @r.get("/orgs/{oid}")
    def get_org(oid: str):
        return need_org(oid)

    @r.post("/orgs/{oid}/sites", status_code=201)
    def create_site(oid: str, b: SiteIn, request: Request):
        need_org(oid)
        sid = new_id("site")
        db.execute("INSERT INTO sites(id,org_id,name,notes,created_at) VALUES(?,?,?,?,?)", (sid, oid, b.name, b.notes, time.time()))
        db.audit(actor(request), "site.created", None, org=oid, site=sid, name=b.name)
        return {"id": sid, "org_id": oid, "name": b.name}

    @r.get("/orgs/{oid}/sites")
    def list_sites(oid: str):
        need_org(oid)
        return db.all("SELECT * FROM sites WHERE org_id=? ORDER BY created_at", (oid,))

    @r.get("/orgs/{oid}/devices")
    def org_devices(oid: str):
        need_org(oid)
        return [{k: d[k] for k in ("id", "name", "address", "transport", "status", "org_id", "site_id")} for d in db.all("SELECT * FROM devices WHERE org_id=?", (oid,))]

    @r.get("/orgs/{oid}/workers")
    def org_workers(oid: str):
        need_org(oid)
        return [workers.view(w) for w in db.all("SELECT * FROM workers WHERE org_id=?", (oid,))]

    def host_of(addr: str) -> str:
        return addr.rsplit(":", 1)[0] if addr.count(":") == 1 else addr.strip("[]")

    @r.get("/orgs/{oid}/fleet")
    def fleet(oid: str):
        """Everything of one client in one call: sites -> devices and workers, with connection state and how the traffic travels."""
        o = need_org(oid)
        sites = {s["id"]: {**s, "devices": [], "workers": []} for s in db.all("SELECT * FROM sites WHERE org_id=?", (oid,))}
        sites[None] = {"id": None, "org_id": oid, "name": "(no site)", "notes": "", "devices": [], "workers": []}
        for d in db.all("SELECT * FROM devices WHERE org_id=?", (oid,)):
            sites.get(d["site_id"], sites[None])["devices"].append({k: d[k] for k in ("id", "name", "address", "transport", "status")} | {"path": classify_path(host_of(d["address"]))})
        for w in db.all("SELECT * FROM workers WHERE org_id=?", (oid,)):
            v = workers.view(w)
            sites.get(w["site_id"], sites[None])["workers"].append({k: v.get(k) for k in ("id", "name", "address", "status", "state", "online")} | {"path": classify_path(host_of(w["address"]))})
        return {"org": o, "sites": [s for s in sites.values() if s["id"] is not None or s["devices"] or s["workers"]]}

    @r.get("/orgs/{oid}/onboarding")
    def onboarding(oid: str, kind: str = "worker"):
        """Step-by-step instructions for connecting the next authorized computer of this client. Contains no secrets and no fixed addresses."""
        need_org(oid)
        if kind not in ("worker", "agent"):
            raise HTTPException(422, "kind must be worker or agent")
        exe = "cit-worker.exe" if kind == "worker" else "cit-agent.exe"
        steps = [
            "Get the client's written permission to manage this computer (record who agreed and when).",
            f"Copy {exe} from the CIT package (USB or download) to the client computer, in a folder of your choice. Compare its SHA-256 with the value shown on the technician PC.",
            "Make sure the computer and the technician PC can reach each other privately: same LAN, or both signed in to the same Tailscale/WireGuard network. Never open a port to the public Internet.",
            f"On the client computer run `{exe} run --bind <this computer's private or Tailscale address>` in a visible window. It prints a fingerprint" + (" and, after `enroll`, a one-time 8-digit code." if kind == "worker" else " and an 8-digit pairing code."),
            "On the technician dashboard open Clients -> this client -> Connect a computer, type the address (a name such as the Tailscale MagicDNS name works), compare the fingerprint group by group with the client's screen, then enter the code.",
            "Assign the new computer to a site; it is then visible only to technicians scoped to this client.",
        ]
        return {"organization": oid, "kind": kind, "steps": steps, "internet": "Use a private overlay network (Tailscale/WireGuard). Public addresses are refused by the controller."}

    def assign(table: str, rid: str, b: AssignIn, request: Request):
        need_org(b.org_id)
        if b.site_id and not db.one("SELECT 1 AS x FROM sites WHERE id=? AND org_id=?", (b.site_id, b.org_id)):
            raise HTTPException(422, "that site does not belong to that organization")
        if not db.one(f"SELECT 1 AS x FROM {table} WHERE id=?", (rid,)):
            raise HTTPException(404, "unknown " + table[:-1])
        db.execute(f"UPDATE {table} SET org_id=?, site_id=? WHERE id=?", (b.org_id, b.site_id, rid))
        db.audit(actor(request), f"{table[:-1]}.assigned", rid if table == "devices" else None, **{table[:-1]: rid}, org=b.org_id, site=b.site_id)
        return {"id": rid, "org_id": b.org_id, "site_id": b.site_id}

    @r.put("/devices/{did}/assign")
    def assign_device(did: str, b: AssignIn, request: Request):
        return assign("devices", did, b, request)

    @r.put("/workers/{wid}/assign")
    def assign_worker(wid: str, b: AssignIn, request: Request):
        return assign("workers", wid, b, request)

    @r.post("/principals", status_code=201)
    def create_principal(b: PrincipalIn, request: Request):
        info, tok = mint_principal(db, b.name, b.role, b.org_ids, actor(request))
        return {**info, "token": tok, "note": "shown once; only a hash is stored"}

    @r.get("/principals")
    def list_principals():
        return [{**{k: p[k] for k in ("id", "name", "role", "created_at", "revoked", "last_used")}, "org_ids": json.loads(p["org_ids"])} for p in db.all("SELECT * FROM principals ORDER BY created_at")]

    @r.delete("/principals/{pid}")
    def revoke_principal(pid: str, request: Request):
        if not db.one("SELECT 1 AS x FROM principals WHERE id=?", (pid,)):
            raise HTTPException(404, "unknown principal")
        db.execute("UPDATE principals SET revoked=1 WHERE id=?", (pid,))
        db.audit(actor(request), "principal.revoked", None, principal=pid)
        return {"ok": True}

    return r
