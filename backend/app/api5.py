"""Monitoring, alerts, notification policies, tickets/SLA and reports. Every route sits under /orgs/{id} or /workers/{id} so tenancy.authorize scopes it."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .db import new_id
from .monitor import METRICS, OPS, SEVERITY, Monitor
from .security import require_token

SLA_HOURS = {"urgent": 4, "high": 8, "normal": 24, "low": 72}


class RuleIn(BaseModel):
    metric: str
    op: str = ">"
    threshold: float = Field(ge=0, le=100)
    consecutive: int = Field(default=3, ge=1, le=60)
    severity: str = "warning"


class PolicyIn(BaseModel):
    channel: str = "email"
    target: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    min_severity: str = "warning"


class TicketIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=4000)
    priority: str = "normal"
    worker_id: str | None = None


class TicketPatch(BaseModel):
    status: str


def build_router(core, monitor: Monitor) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def actor(request: Request) -> str:
        return request.state.principal.id

    def need_org(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")

    @r.get("/workers/{wid}/metrics")
    def metrics(wid: str, hours: float = 24):
        return monitor.history(wid, min(max(hours, 0.1), 24 * SAMPLE_DAYS))

    @r.get("/orgs/{oid}/alerts")
    def alerts(oid: str, state: str | None = None, limit: int = 200):
        need_org(oid)
        q, a = "SELECT * FROM alerts WHERE org_id=?", [oid]
        if state:
            q += " AND state=?"; a.append(state)
        return db.all(q + " ORDER BY last_seen DESC LIMIT ?", tuple(a) + (min(max(limit, 1), 1000),))

    @r.post("/orgs/{oid}/alerts/{aid}/ack")
    def ack(oid: str, aid: str, request: Request):
        if not db.one("SELECT 1 AS x FROM alerts WHERE id=? AND org_id=?", (aid, oid)):
            raise HTTPException(404, "unknown alert")
        return {"acknowledged": monitor.ack(aid, actor(request))}

    @r.get("/orgs/{oid}/alert-rules")
    def rules(oid: str):
        need_org(oid)
        return db.all("SELECT * FROM alert_rules WHERE org_id=?", (oid,))

    @r.post("/orgs/{oid}/alert-rules", status_code=201)
    def add_rule(oid: str, b: RuleIn, request: Request):
        need_org(oid)
        if b.metric not in METRICS or b.op not in OPS or b.severity not in SEVERITY:
            raise HTTPException(422, f"metric in {METRICS}, op in {tuple(OPS)}, severity in {tuple(SEVERITY)}")
        rid = new_id("rul")
        db.execute("INSERT INTO alert_rules(id,org_id,metric,op,threshold,consecutive,severity,created_at) VALUES(?,?,?,?,?,?,?,?)", (rid, oid, b.metric, b.op, b.threshold, b.consecutive, b.severity, time.time()))
        db.audit(actor(request), "alert_rule.created", None, org=oid, rule=rid)
        return {"id": rid}

    @r.delete("/orgs/{oid}/alert-rules/{rid}")
    def del_rule(oid: str, rid: str, request: Request):
        db.execute("DELETE FROM alert_rules WHERE id=? AND org_id=?", (rid, oid))
        db.audit(actor(request), "alert_rule.deleted", None, org=oid, rule=rid)
        return {"ok": True}

    @r.get("/orgs/{oid}/notify-policies")
    def policies(oid: str):
        need_org(oid)
        return db.all("SELECT * FROM notify_policies WHERE org_id=?", (oid,))

    @r.post("/orgs/{oid}/notify-policies", status_code=201)
    def add_policy(oid: str, b: PolicyIn, request: Request):
        need_org(oid)
        if b.channel != "email" or b.min_severity not in SEVERITY:
            raise HTTPException(422, "channel must be email; min_severity info|warning|critical")
        pid = new_id("pol")
        db.execute("INSERT INTO notify_policies(id,org_id,channel,target,min_severity,created_at) VALUES(?,?,?,?,?,?)", (pid, oid, b.channel, b.target, b.min_severity, time.time()))
        db.audit(actor(request), "notify_policy.created", None, org=oid, policy=pid)
        return {"id": pid}

    @r.delete("/orgs/{oid}/notify-policies/{pid}")
    def del_policy(oid: str, pid: str, request: Request):
        db.execute("DELETE FROM notify_policies WHERE id=? AND org_id=?", (pid, oid))
        db.audit(actor(request), "notify_policy.deleted", None, org=oid, policy=pid)
        return {"ok": True}

    @r.get("/orgs/{oid}/notifications")
    def notifications(oid: str):
        need_org(oid)
        return db.all("SELECT * FROM notifications WHERE org_id=? ORDER BY ts DESC LIMIT 200", (oid,))

    @r.get("/orgs/{oid}/reports/fleet")
    def fleet_report(oid: str, hours: float = 24):
        need_org(oid)
        return monitor.fleet_report(oid, min(max(hours, 1), 24 * SAMPLE_DAYS))

    # ---- tickets, incidents and SLA
    @r.post("/orgs/{oid}/tickets", status_code=201)
    def mk_ticket(oid: str, b: TicketIn, request: Request):
        need_org(oid)
        if b.priority not in SLA_HOURS:
            raise HTTPException(422, f"priority must be one of {tuple(SLA_HOURS)}")
        if b.worker_id and not db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (b.worker_id, oid)):
            raise HTTPException(422, "that machine does not belong to this client")
        tid, now = new_id("tkt"), time.time()
        db.execute("INSERT INTO tickets(id,org_id,worker_id,title,body,priority,created_by,created_at,due_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (tid, oid, b.worker_id, b.title, b.body, b.priority, actor(request), now, now + SLA_HOURS[b.priority] * 3600))
        db.audit(actor(request), "ticket.created", None, org=oid, ticket=tid)
        return {"id": tid, "due_at": now + SLA_HOURS[b.priority] * 3600}

    @r.get("/orgs/{oid}/tickets")
    def tickets(oid: str, status: str | None = None):
        need_org(oid)
        q, a = "SELECT * FROM tickets WHERE org_id=?", [oid]
        if status:
            q += " AND status=?"; a.append(status)
        return db.all(q + " ORDER BY created_at DESC LIMIT 500", tuple(a))

    @r.patch("/orgs/{oid}/tickets/{tid}")
    def set_ticket(oid: str, tid: str, b: TicketPatch, request: Request):
        if b.status not in ("open", "in_progress", "resolved", "closed"):
            raise HTTPException(422, "status must be open|in_progress|resolved|closed")
        if not db.one("SELECT 1 AS x FROM tickets WHERE id=? AND org_id=?", (tid, oid)):
            raise HTTPException(404, "unknown ticket")
        db.execute("UPDATE tickets SET status=?, resolved_at=CASE WHEN ? IN ('resolved','closed') THEN COALESCE(resolved_at, ?) ELSE NULL END WHERE id=?", (b.status, b.status, time.time(), tid))
        db.audit(actor(request), "ticket.updated", None, org=oid, ticket=tid, status=b.status)
        return {"ok": True}

    @r.post("/orgs/{oid}/alerts/{aid}/ticket", status_code=201)
    def alert_to_ticket(oid: str, aid: str, request: Request):
        a = db.one("SELECT * FROM alerts WHERE id=? AND org_id=?", (aid, oid))
        if not a:
            raise HTTPException(404, "unknown alert")
        pr = "urgent" if a["severity"] == "critical" else "normal"
        tid, now = new_id("tkt"), time.time()
        db.execute("INSERT INTO tickets(id,org_id,worker_id,alert_id,title,body,priority,created_by,created_at,due_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (tid, oid, a["worker_id"], aid, f"[{a['kind']}] {a['message'][:150]}", "", pr, actor(request), now, now + SLA_HOURS[pr] * 3600))
        db.audit(actor(request), "ticket.from_alert", None, org=oid, ticket=tid, alert=aid)
        return {"id": tid}

    @r.get("/orgs/{oid}/reports/sla")
    def sla(oid: str):
        need_org(oid)
        t = db.all("SELECT * FROM tickets WHERE org_id=?", (oid,))
        done = [x for x in t if x["resolved_at"]]
        met = [x for x in done if x["resolved_at"] <= x["due_at"]]
        now = time.time()
        return {"tickets": len(t), "resolved": len(done), "met_sla": len(met), "sla_pct": round(100 * len(met) / len(done), 1) if done else None,
                "overdue_open": len([x for x in t if not x["resolved_at"] and x["due_at"] < now])}

    return r


SAMPLE_DAYS = 14
