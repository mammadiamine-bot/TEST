"""Fleet monitoring: bounded metric history, threshold rules, deduplicated alerts, notification policies, retention.

Fed by Workers._set (every health poll). Email goes through an injectable sender; without SMTP settings the notification is recorded
as 'not_sent_no_smtp' - it is never reported as delivered.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Callable

from .db import Db, new_id

METRICS = ("cpu", "ram", "disk")
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b}
SEVERITY = {"info": 0, "warning": 1, "critical": 2}
SAMPLE_EVERY = 30.0          # at most one stored sample per worker per 30 s
OFFLINE_AFTER = 3            # consecutive failed polls before an 'offline' alert (10 s poll => ~30 s)
SAMPLE_RETENTION_DAYS = 14   # bounded storage
ALERT_RETENTION_DAYS = 180


def smtp_sender() -> Callable[[str, str, str], None] | None:
    host = os.environ.get("CIT_SMTP_HOST")
    if not host:
        return None
    port, user, pw = int(os.environ.get("CIT_SMTP_PORT", "587")), os.environ.get("CIT_SMTP_USER"), os.environ.get("CIT_SMTP_PASSWORD")
    sender = os.environ.get("CIT_SMTP_FROM", user or "cit@localhost")

    def send(to: str, subject: str, body: str) -> None:
        m = EmailMessage(); m["From"], m["To"], m["Subject"] = sender, to, subject; m.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, pw or "")
            s.send_message(m)
    return send


class Monitor:
    def __init__(self, db: Db, sender: Callable[[str, str, str], None] | None | str = "env"):
        self.db = db
        self.sender = smtp_sender() if sender == "env" else sender
        self.fails: dict[str, int] = {}
        self.last_sample: dict[str, float] = {}
        self.last_prune = 0.0

    # ---- ingestion
    def on_poll(self, wid: str, online: bool, st: dict | None) -> None:
        w = self.db.one("SELECT org_id FROM workers WHERE id=?", (wid,))
        if not w or not w["org_id"]:
            return  # unassigned machines are not monitored for any client
        org, now = w["org_id"], time.time()
        self.fails[wid] = 0 if online else self.fails.get(wid, 0) + 1
        if now - self.last_sample.get(wid, 0) >= SAMPLE_EVERY or (not online and self.fails[wid] == 1):
            self.last_sample[wid] = now
            self.db.execute("INSERT INTO samples(worker_id,ts,online,cpu,ram,disk) VALUES(?,?,?,?,?,?)",
                            (wid, now, int(online), *(((st or {}).get(m) if online else None) for m in METRICS)))
        if online:
            self._resolve(f"offline:{wid}")
            for rule in self.db.all("SELECT * FROM alert_rules WHERE org_id=? AND enabled=1", (org,)):
                self._eval_rule(rule, wid, org)
        elif self.fails[wid] >= OFFLINE_AFTER:
            self._raise(org, wid, None, "offline", "critical", "Device is offline (no response to health checks)", f"offline:{wid}")
        if now - self.last_prune > 3600:
            self.prune(); self.last_prune = now

    def _eval_rule(self, rule: dict, wid: str, org: str) -> None:
        key = f"rule:{rule['id']}:{wid}"
        rows = self.db.all("SELECT * FROM samples WHERE worker_id=? AND online=1 ORDER BY ts DESC LIMIT ?", (wid, rule["consecutive"]))
        vals = [r[rule["metric"]] for r in rows if r[rule["metric"]] is not None]
        if len(vals) < rule["consecutive"]:
            return
        if all(OPS[rule["op"]](v, rule["threshold"]) for v in vals):
            self._raise(org, wid, rule["id"], "threshold", rule["severity"], f"{rule['metric'].upper()} {rule['op']} {rule['threshold']:g}% for {rule['consecutive']} checks (now {vals[0]:.0f}%)", key)
        elif not OPS[rule["op"]](vals[0], rule["threshold"]):
            self._resolve(key)

    # ---- alerts (deduplicated)
    def _raise(self, org: str, wid: str, rule_id: str | None, kind: str, severity: str, msg: str, dedup: str) -> None:
        now = time.time()
        a = self.db.one("SELECT * FROM alerts WHERE dedup=? AND state IN ('open','acknowledged')", (dedup,))
        if a:
            self.db.execute("UPDATE alerts SET last_seen=?, count=count+1, message=? WHERE id=?", (now, msg, a["id"]))
            return
        aid = new_id("alr")
        self.db.execute("INSERT INTO alerts(id,org_id,worker_id,rule_id,kind,severity,message,first_seen,last_seen,dedup) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (aid, org, wid, rule_id, kind, severity, msg, now, now, dedup))
        self.db.audit("system", "alert.opened", None, org=org, worker=wid, alert=aid, kind=kind, severity=severity)
        self._notify(org, aid, severity, msg, wid)

    def _resolve(self, dedup: str) -> None:
        for a in self.db.all("SELECT * FROM alerts WHERE dedup=? AND state IN ('open','acknowledged')", (dedup,)):
            self.db.execute("UPDATE alerts SET state='resolved', resolved_at=? WHERE id=?", (time.time(), a["id"]))
            self.db.audit("system", "alert.resolved", None, org=a["org_id"], worker=a["worker_id"], alert=a["id"])

    def ack(self, aid: str, actor: str) -> bool:
        n = self.db.execute("UPDATE alerts SET state='acknowledged', acked_by=?, acked_at=? WHERE id=? AND state='open'", (actor, time.time(), aid)).rowcount
        if n:
            self.db.audit(actor, "alert.acknowledged", None, alert=aid)
        return bool(n)

    # ---- notifications per client policy
    def _notify(self, org: str, aid: str, severity: str, msg: str, wid: str) -> None:
        for pol in self.db.all("SELECT * FROM notify_policies WHERE org_id=?", (org,)):
            if SEVERITY[severity] < SEVERITY[pol["min_severity"]] or pol["channel"] != "email":
                continue
            status, detail = "sent", ""
            if not self.sender:
                status, detail = "not_sent_no_smtp", "SMTP is not configured on the server (CIT_SMTP_HOST)"
            else:
                try:
                    w = self.db.one("SELECT name FROM workers WHERE id=?", (wid,))
                    self.sender(pol["target"], f"[CIT {severity.upper()}] {w['name'] if w else wid}", msg)
                except Exception as e:  # noqa: BLE001
                    status, detail = "failed", f"{type(e).__name__}"
            self.db.execute("INSERT INTO notifications(id,org_id,alert_id,channel,target,status,detail,ts) VALUES(?,?,?,?,?,?,?,?)",
                            (new_id("ntf"), org, aid, "email", pol["target"], status, detail, time.time()))

    # ---- retention
    def prune(self) -> None:
        now = time.time()
        self.db.execute("DELETE FROM samples WHERE ts<?", (now - SAMPLE_RETENTION_DAYS * 86400,))
        self.db.execute("DELETE FROM alerts WHERE state='resolved' AND resolved_at<?", (now - ALERT_RETENTION_DAYS * 86400,))
        self.db.execute("DELETE FROM notifications WHERE ts<?", (now - ALERT_RETENTION_DAYS * 86400,))

    # ---- reads
    def history(self, wid: str, hours: float, points: int = 200) -> list[dict]:
        rows = self.db.all("SELECT ts,online,cpu,ram,disk FROM samples WHERE worker_id=? AND ts>=? ORDER BY ts", (wid, time.time() - hours * 3600))
        step = max(1, len(rows) // points)
        return rows[::step]

    def fleet_report(self, org: str, hours: float = 24) -> dict:
        since, out = time.time() - hours * 3600, []
        for w in self.db.all("SELECT id,name,site_id FROM workers WHERE org_id=?", (org,)):
            s = self.db.all("SELECT online,cpu,ram,disk FROM samples WHERE worker_id=? AND ts>=?", (w["id"], since))
            on = [x for x in s if x["online"]]
            avg = lambda k: round(sum(x[k] for x in on if x[k] is not None) / max(1, len([x for x in on if x[k] is not None])), 1) if on else None  # noqa: E731
            out.append({"worker": w["id"], "name": w["name"], "samples": len(s), "availability_pct": round(100 * len(on) / len(s), 2) if s else None,
                        "avg_cpu": avg("cpu"), "avg_ram": avg("ram"), "avg_disk": avg("disk"),
                        "open_alerts": self.db.one("SELECT COUNT(*) n FROM alerts WHERE worker_id=? AND state IN ('open','acknowledged')", (w["id"],))["n"]})
        known = [x["availability_pct"] for x in out if x["availability_pct"] is not None]
        return {"org": org, "window_hours": hours, "workers": out, "fleet_availability_pct": round(sum(known) / len(known), 2) if known else None,
                "open_alerts": sum(x["open_alerts"] for x in out)}
