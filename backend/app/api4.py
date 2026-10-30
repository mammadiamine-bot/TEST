"""Clients (organizations), sites, assignment of devices/workers, and scoped principals. Access rules live in tenancy.py."""
from __future__ import annotations

import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .db import new_id
from .security import require_token
from .netpolicy import classify_path
from .tenancy import FAIL_LIMIT, FAIL_WINDOW, ROLES, _fails, mint_principal, org_active, token_hash


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=500)


class AssignIn(BaseModel):
    org_id: str
    site_id: str | None = None


class InviteIn(BaseModel):
    role: str
    days: int = Field(default=7, ge=1, le=30)
    note: str = Field(default="", max_length=200)


class AcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=80)


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=20)


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class MembersIn(BaseModel):
    members: list[dict]


class OffboardIn(BaseModel):
    confirm_name: str
    retention_days: int = Field(default=30, ge=0, le=3650)


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
        getattr(core, "billing", None) and core.billing.ensure(oid)  # every client starts on a free trial
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
        if not request.state.principal.is_owner and b.org_id not in request.state.principal.orgs:
            raise HTTPException(403, "not permitted")  # an administrator can reorganize only inside their own clients
        if b.site_id and not db.one("SELECT 1 AS x FROM sites WHERE id=? AND org_id=?", (b.site_id, b.org_id)):
            raise HTTPException(422, "that site does not belong to that organization")
        if not db.one(f"SELECT 1 AS x FROM {table} WHERE id=?", (rid,)):
            raise HTTPException(404, "unknown " + table[:-1])
        cur = db.one(f"SELECT org_id FROM {table} WHERE id=?", (rid,))
        if cur["org_id"] != b.org_id and getattr(core, "billing", None):
            core.billing.check_capacity(b.org_id)  # plan device limit (402 when exceeded)
        db.execute(f"UPDATE {table} SET org_id=?, site_id=? WHERE id=?", (b.org_id, b.site_id, rid))
        db.audit(actor(request), f"{table[:-1]}.assigned", rid if table == "devices" else None, **{table[:-1]: rid}, org=b.org_id, site=b.site_id)
        return {"id": rid, "org_id": b.org_id, "site_id": b.site_id}

    @r.put("/devices/{did}/assign")
    def assign_device(did: str, b: AssignIn, request: Request):
        return assign("devices", did, b, request)

    @r.put("/workers/{wid}/assign")
    def assign_worker(wid: str, b: AssignIn, request: Request):
        return assign("workers", wid, b, request)

    def visible(p, row) -> bool:
        return p.is_owner or set(json.loads(row["org_ids"])) <= p.orgs

    @r.get("/me")
    def me(request: Request):
        p = request.state.principal
        return {"id": p.id, "role": p.role, "org_ids": None if p.is_owner else sorted(p.orgs)}

    @r.post("/principals", status_code=201)
    def create_principal(b: PrincipalIn, request: Request):
        p = request.state.principal
        if not p.is_owner and (b.role == "administrator" or not set(b.org_ids) <= p.orgs):
            raise HTTPException(403, "not permitted")  # administrators cannot create administrators or reach outside their clients
        info, tok = mint_principal(db, b.name, b.role, b.org_ids, actor(request))
        return {**info, "token": tok, "note": "shown once; only a hash is stored"}

    @r.get("/principals")
    def list_principals(request: Request):
        p = request.state.principal
        return [{**{k: x[k] for k in ("id", "name", "role", "created_at", "revoked", "last_used")}, "org_ids": json.loads(x["org_ids"])}
                for x in db.all("SELECT * FROM principals ORDER BY created_at") if visible(p, x)]

    def own_principal(request: Request, pid: str) -> dict:
        row = db.one("SELECT * FROM principals WHERE id=?", (pid,))
        p = request.state.principal
        if not row or not visible(p, row) or (row["role"] == "administrator" and not p.is_owner):
            raise HTTPException(403, "not permitted")  # same answer whether it exists or not
        return row

    @r.delete("/principals/{pid}")
    def revoke_principal(pid: str, request: Request):
        own_principal(request, pid)
        db.execute("UPDATE principals SET revoked=1 WHERE id=?", (pid,))
        db.audit(actor(request), "principal.revoked", None, principal=pid)
        return {"ok": True}

    @r.post("/principals/{pid}/rotate")
    def rotate_principal(pid: str, request: Request):
        """New token, old token dead immediately. The new one is shown once."""
        own_principal(request, pid)
        tok = "cit_" + secrets.token_urlsafe(32)
        db.execute("UPDATE principals SET token_hash=?, revoked=0 WHERE id=?", (token_hash(tok), pid))
        db.audit(actor(request), "principal.rotated", None, principal=pid)
        return {"id": pid, "token": tok, "note": "shown once; the previous token no longer works"}

    # ---- secure invitations (single use, expiring, hash-only)
    @r.post("/orgs/{oid}/invitations", status_code=201)
    def invite(oid: str, b: InviteIn, request: Request):
        need_org(oid)
        p = request.state.principal
        if b.role not in ROLES:
            raise HTTPException(422, f"role must be one of {ROLES}")
        if b.role == "administrator" and not p.is_owner:
            raise HTTPException(403, "not permitted")
        tok = "inv_" + secrets.token_urlsafe(24)
        iid = new_id("inv")
        db.execute("INSERT INTO invitations(id,org_id,role,token_hash,created_by,created_at,expires_at,note) VALUES(?,?,?,?,?,?,?,?)",
                   (iid, oid, b.role, token_hash(tok), actor(request), time.time(), time.time() + b.days * 86400, b.note))
        db.audit(actor(request), "invitation.created", None, org=oid, role=b.role, invitation=iid)
        return {"id": iid, "token": tok, "expires_in_days": b.days, "note": "give this to the person once; it can be used one time"}

    @r.get("/orgs/{oid}/invitations")
    def invitations(oid: str):
        need_org(oid)
        return db.all("SELECT id,org_id,role,created_by,created_at,expires_at,used_at,note FROM invitations WHERE org_id=? ORDER BY created_at DESC", (oid,))

    @r.delete("/orgs/{oid}/invitations/{iid}")
    def revoke_invite(oid: str, iid: str, request: Request):
        db.execute("UPDATE invitations SET expires_at=0 WHERE id=? AND org_id=? AND used_at IS NULL", (iid, oid))
        db.audit(actor(request), "invitation.revoked", None, org=oid, invitation=iid)
        return {"ok": True}

    # ---- tags and groups
    def _set_tags(kind: str, rid: str, tags: list[str]):
        if not db.one(f"SELECT 1 AS x FROM {kind} WHERE id=?", (rid,)):
            raise HTTPException(404, "unknown resource")
        clean = sorted({t.strip().lower()[:32] for t in tags if t.strip()})
        db.execute("DELETE FROM tags WHERE kind=? AND rid=?", (kind, rid))
        for t in clean:
            db.execute("INSERT INTO tags(kind,rid,tag) VALUES(?,?,?)", (kind, rid, t))
        return {"tags": clean}

    @r.put("/devices/{rid}/tags")
    def tag_device(rid: str, b: TagsIn):
        return _set_tags("devices", rid, b.tags)

    @r.put("/workers/{rid}/tags")
    def tag_worker(rid: str, b: TagsIn):
        return _set_tags("workers", rid, b.tags)

    @r.post("/orgs/{oid}/groups", status_code=201)
    def mk_group(oid: str, b: GroupIn, request: Request):
        need_org(oid)
        gid = new_id("grp")
        if db.one("SELECT 1 AS x FROM groups WHERE org_id=? AND name=?", (oid, b.name)):
            raise HTTPException(409, "a group with that name already exists")
        db.execute("INSERT INTO groups(id,org_id,name,created_at) VALUES(?,?,?,?)", (gid, oid, b.name, time.time()))
        db.audit(actor(request), "group.created", None, org=oid, group=gid)
        return {"id": gid, "name": b.name}

    @r.get("/orgs/{oid}/groups")
    def groups(oid: str):
        need_org(oid)
        return [{**g, "members": db.all("SELECT kind,rid FROM group_members WHERE group_id=?", (g["id"],))} for g in db.all("SELECT * FROM groups WHERE org_id=?", (oid,))]

    @r.put("/orgs/{oid}/groups/{gid}")
    def set_members(oid: str, gid: str, b: MembersIn, request: Request):
        if not db.one("SELECT 1 AS x FROM groups WHERE id=? AND org_id=?", (gid, oid)):
            raise HTTPException(404, "unknown group")
        ok = []
        for m in b.members:
            kind, rid = m.get("kind"), m.get("rid")
            if kind not in ("devices", "workers") or not db.one(f"SELECT 1 AS x FROM {kind} WHERE id=? AND org_id=?", (rid, oid)):
                raise HTTPException(422, "members must be devices/workers of this organization")  # a group can never pull in another client's machine
            ok.append((gid, kind, rid))
        db.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
        for t in ok:
            db.execute("INSERT INTO group_members(group_id,kind,rid) VALUES(?,?,?)", t)
        db.audit(actor(request), "group.members_set", None, org=oid, group=gid, count=len(ok))
        return {"members": len(ok)}

    # ---- customer-specific audit trail
    @r.get("/orgs/{oid}/audit")
    def org_audit(oid: str, limit: int = 200):
        need_org(oid)
        conds, args = ["detail LIKE ?"], [f'%"org": "{oid}"%']
        for w in db.all("SELECT id FROM workers WHERE org_id=?", (oid,)):
            conds.append("detail LIKE ?"); args.append(f'%"worker": "{w["id"]}"%')
        devs = [d["id"] for d in db.all("SELECT id FROM devices WHERE org_id=?", (oid,))]
        if devs:
            conds.append("device_id IN (%s)" % ",".join("?" * len(devs))); args += devs
        return db.all("SELECT seq,ts,actor,action,device_id,detail FROM audit WHERE " + " OR ".join(conds) + " ORDER BY seq DESC LIMIT ?", tuple(args) + (min(max(limit, 1), 1000),))

    # ---- offboarding and retention
    @r.post("/orgs/{oid}/offboard")
    def offboard(oid: str, b: OffboardIn, request: Request):
        o = need_org(oid)
        if b.confirm_name != o["name"]:
            raise HTTPException(422, "type the exact client name to confirm")
        now = time.time()
        db.execute("UPDATE orgs SET status='offboarded', offboarded_at=?, retention_until=? WHERE id=?", (now, now + b.retention_days * 86400, oid))
        revoked = 0
        for pr in db.all("SELECT * FROM principals WHERE revoked=0"):
            ids = json.loads(pr["org_ids"])
            if oid in ids:
                rest = [i for i in ids if i != oid]
                if rest:
                    db.execute("UPDATE principals SET org_ids=? WHERE id=?", (json.dumps(rest), pr["id"]))
                else:
                    db.execute("UPDATE principals SET revoked=1 WHERE id=?", (pr["id"],)); revoked += 1
        db.execute("UPDATE invitations SET expires_at=0 WHERE org_id=? AND used_at IS NULL", (oid,))
        results = []
        for w in db.all("SELECT id FROM workers WHERE org_id=?", (oid,)):
            try:
                workers.remove(w["id"], revoke=True); results.append({"worker": w["id"], "revoked": True})
            except Exception as e:  # noqa: BLE001 - offline workers: credential must be revoked on the machine later
                results.append({"worker": w["id"], "revoked": False, "error": str(e)[:120]})
        db.audit(actor(request), "org.offboarded", None, org=oid, retention_days=b.retention_days, principals_revoked=revoked, workers=len(results))
        return {"status": "offboarded", "retention_until": now + b.retention_days * 86400, "principals_revoked": revoked, "workers": results}

    @r.post("/orgs/{oid}/purge")
    def purge(oid: str, b: OffboardIn, request: Request):
        o = need_org(oid)
        if b.confirm_name != o["name"] or o["status"] != "offboarded" or time.time() < (o["retention_until"] or 0):
            raise HTTPException(409, "only an offboarded client whose retention period has ended can be purged, with its exact name typed")
        for t in ("alerts", "alert_rules", "notify_policies", "notifications", "tickets", "jobs", "schedules", "ai_runs", "org_settings", "invitations", "usage", "subscriptions", "sites"):
            db.execute(f"DELETE FROM {t} WHERE org_id=?", (oid,))
        db.execute("DELETE FROM job_runs WHERE job_id NOT IN (SELECT id FROM jobs)")
        db.execute("DELETE FROM group_members WHERE group_id IN (SELECT id FROM groups WHERE org_id=?)", (oid,)); db.execute("DELETE FROM groups WHERE org_id=?", (oid,))
        db.execute("DELETE FROM samples WHERE worker_id IN (SELECT id FROM workers WHERE org_id=?)", (oid,))
        db.execute("DELETE FROM workers WHERE org_id=?", (oid,)); db.execute("DELETE FROM devices WHERE org_id=?", (oid,)); db.execute("DELETE FROM orgs WHERE id=?", (oid,))
        db.audit(actor(request), "org.purged", None, org=oid)  # the audit chain itself is kept: it is append-only evidence
        return {"purged": True}

    # public: redeem an invitation (single use). Rate-limited by client address.
    pub = APIRouter()

    @pub.post("/invitations/accept", status_code=201)
    def accept(b: AcceptIn, request: Request):
        fails = _fails(request)
        now = time.time()
        fails[:] = [t for t in fails if now - t < FAIL_WINDOW]
        if len(fails) >= FAIL_LIMIT:
            raise HTTPException(429, "too many failed attempts; try again later")
        row = db.one("SELECT * FROM invitations WHERE token_hash=?", (token_hash(b.token),))
        if not row or row["used_at"] is not None or row["expires_at"] < now or not org_active(db, row["org_id"]):
            fails.append(now)
            raise HTTPException(410, "this invitation is invalid, used or expired")
        db.execute("UPDATE invitations SET used_at=? WHERE id=?", (now, row["id"]))
        info, tok = mint_principal(db, b.name, row["role"], [row["org_id"]], "invitation:" + row["id"])
        return {**info, "token": tok, "note": "shown once; only a hash is stored"}

    r.public = pub
    return r
