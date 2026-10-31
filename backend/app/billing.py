"""Plans, entitlements, signed licenses, usage metering and invoice-ready data.

No payment is ever taken here: there is no payment-provider integration (it needs the owner's merchant credentials). Invoices are
DRAFT records computed from list prices; the owner issues/collects them outside this system until a provider is connected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException

from .db import Db, new_id

# list prices are placeholders the owner edits; they are not a promise of what any customer is charged
PLANS: dict[str, dict] = {
    "trial": {"price_month": 0, "max_devices": 5, "retention_days": 14, "ai_tokens_month": 50_000, "features": ["jobs", "alerts", "scheduler", "ai"]},
    "basic": {"price_month": 3.0, "max_devices": 10, "retention_days": 30, "ai_tokens_month": 0, "features": ["jobs", "alerts"]},
    "professional": {"price_month": 6.0, "max_devices": 50, "retention_days": 90, "ai_tokens_month": 500_000, "features": ["jobs", "alerts", "scheduler", "ai", "email_alerts"]},
    "business": {"price_month": 9.0, "max_devices": 500, "retention_days": 365, "ai_tokens_month": 5_000_000, "features": ["jobs", "alerts", "scheduler", "ai", "email_alerts", "sla_reports"]},
}
TRIAL_DAYS = 14
PERIOD = 30 * 86400


class Billing:
    def __init__(self, db: Db, data_dir: Path, protector=None):
        """The signing key is created atomically owner-only (0600) and, where the OS offers it (Windows DPAPI), stored encrypted to the user."""
        self.db = db
        from .protect import default_protector
        prot = protector or default_protector()
        f = Path(data_dir) / "license.key"
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            raw = secrets.token_hex(32).encode()
            try:
                blob = b"P1" + prot.protect(raw)
            except Exception:  # noqa: BLE001 - no OS-backed store: fall back to a 0600 file (documented limitation)
                blob = b"R1" + raw
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
        blob = f.read_bytes()
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        self._key = bytes.fromhex((prot.unprotect(blob[2:]) if blob[:2] == b"P1" else blob[2:]).decode())

    # ---- subscription lifecycle
    def ensure(self, org: str) -> dict:
        s = self.db.one("SELECT * FROM subscriptions WHERE org_id=?", (org,))
        if s:
            return s
        now = time.time()
        self.db.execute("INSERT INTO subscriptions(org_id,plan,status,trial_ends,period_start,period_end,updated_at) VALUES(?,?,?,?,?,?,?)", (org, "trial", "trial", now + TRIAL_DAYS * 86400, now, now + TRIAL_DAYS * 86400, now))
        self._history(org, "system", f"trial started ({TRIAL_DAYS} days)")
        return self.db.one("SELECT * FROM subscriptions WHERE org_id=?", (org,))

    def _history(self, org: str, actor: str, change: str) -> None:
        self.db.execute("INSERT INTO subscription_history(org_id,ts,actor,change) VALUES(?,?,?,?)", (org, time.time(), actor, change))
        self.db.audit(actor, "subscription.changed", None, org=org, change=change)

    def current(self, org: str) -> dict:
        s = self.ensure(org)
        now = time.time()
        status = s["status"]
        if status in ("trial", "active") and now > s["period_end"]:
            status = "expired"
            self.db.execute("UPDATE subscriptions SET status='expired', updated_at=? WHERE org_id=?", (now, org)); self._history(org, "system", "expired")
        plan = PLANS[s["plan"]]
        return {**s, "status": status, "entitlements": plan, "usage": self.usage(org)}

    def set_plan(self, org: str, plan: str, actor: str, days: int = 30) -> dict:
        if plan not in PLANS or plan == "trial":
            raise HTTPException(422, f"plan must be one of {[p for p in PLANS if p != 'trial']}")
        devices = self.devices(org)
        if devices > PLANS[plan]["max_devices"]:
            raise HTTPException(409, f"{devices} machines are assigned; the {plan} plan allows {PLANS[plan]['max_devices']}. Remove some first.")  # no silent over-limit downgrade
        old = self.ensure(org)["plan"]
        now = time.time()
        self.db.execute("UPDATE subscriptions SET plan=?, status='active', period_start=?, period_end=?, updated_at=? WHERE org_id=?", (plan, now, now + days * 86400, now, org))
        self._history(org, actor, f"plan {old} -> {plan} for {days} days")
        return self.current(org)

    # ---- signed licenses (offline-verifiable; HMAC with a key that never leaves the server)
    def issue_license(self, org: str, plan: str, days: int, actor: str) -> str:
        if plan not in PLANS or plan == "trial":
            raise HTTPException(422, "invalid plan")
        payload = json.dumps({"id": new_id("lic"), "org": org, "plan": plan, "days": days, "iat": int(time.time())}, separators=(",", ":")).encode()
        sig = hmac.new(self._key, payload, hashlib.sha256).digest()
        self.db.audit(actor, "license.issued", None, org=org, plan=plan, days=days)
        return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")

    def activate(self, org: str, token: str, actor: str) -> dict:
        try:
            p64, s64 = token.split(".")
            payload = base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4)); sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
            data = json.loads(payload)
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "malformed license")
        if not hmac.compare_digest(sig, hmac.new(self._key, payload, hashlib.sha256).digest()) or data.get("org") != org:
            raise HTTPException(403, "license is not valid for this client")
        if self.db.one("SELECT 1 AS x FROM subscription_history WHERE org_id=? AND change LIKE ?", (org, f"%{data['id']}%")):
            raise HTTPException(409, "this license was already activated")
        out = self.set_plan(org, data["plan"], actor, int(data["days"]))
        self._history(org, actor, f"license {data['id']} activated")
        return out

    # ---- entitlements
    def devices(self, org: str) -> int:
        return self.db.one("SELECT (SELECT COUNT(*) FROM workers WHERE org_id=?)+(SELECT COUNT(*) FROM devices WHERE org_id=? AND transport!='local') n", (org, org))["n"]

    def entitle(self, org: str, feature: str) -> None:
        """Raises 402 when the client's plan/subscription does not include the feature. Fails CLOSED."""
        cur = self.current(org)
        if cur["status"] == "expired" or feature not in cur["entitlements"]["features"]:
            raise HTTPException(402, f"your plan does not include '{feature}' or the subscription has expired")
        if feature == "jobs":
            self._bump(org, "jobs", 1)
        if feature == "ai" and cur["entitlements"]["ai_tokens_month"] <= cur["usage"]["ai_tokens_month"]:
            raise HTTPException(402, "the monthly AI allowance for this plan is used up")

    def check_capacity(self, org: str) -> None:
        cur = self.current(org)
        if self.devices(org) + 1 > cur["entitlements"]["max_devices"] or cur["status"] == "expired":
            raise HTTPException(402, f"the {cur['plan']} plan allows {cur['entitlements']['max_devices']} machines (or the subscription expired)")

    # ---- usage metering
    def _bump(self, org: str, col: str, n: int) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        self.db.execute("INSERT OR IGNORE INTO usage(org_id,day,devices) VALUES(?,?,?)", (org, day, self.devices(org)))
        self.db.execute(f"UPDATE usage SET {col}={col}+?, devices=MAX(devices, ?) WHERE org_id=? AND day=?", (n, self.devices(org), org, day))

    def add_ai_tokens(self, org: str, n: int) -> None:
        self._bump(org, "ai_tokens", n)

    def snapshot_usage(self) -> None:
        for o in self.db.all("SELECT id FROM orgs WHERE COALESCE(status,'active')='active'"):
            self._bump(o["id"], "jobs", 0)

    def usage(self, org: str, month: str | None = None) -> dict:
        month = month or time.strftime("%Y-%m", time.gmtime())
        r = self.db.one("SELECT COALESCE(MAX(devices),0) peak, COALESCE(SUM(jobs),0) jobs, COALESCE(SUM(ai_tokens),0) ai FROM usage WHERE org_id=? AND day LIKE ?", (org, month + "%"))
        return {"month": month, "devices_now": self.devices(org), "devices_peak": r["peak"], "jobs": r["jobs"], "ai_tokens_month": r["ai"]}

    # ---- invoice-ready data (draft; never charged here)
    def invoice_data(self, org: str, month: str | None = None) -> dict:
        s, u = self.current(org), self.usage(org, month)
        plan = PLANS[s["plan"]]
        lines = [{"description": f"{s['plan'].title()} plan - {u['devices_peak']} machines (peak) x list price", "quantity": u["devices_peak"], "unit_price": plan["price_month"], "amount": round(u["devices_peak"] * plan["price_month"], 2)}]
        return {"organization": org, "period": u["month"], "plan": s["plan"], "currency": "USD", "lines": lines, "total": round(sum(x["amount"] for x in lines), 2),
                "usage": u, "status": "draft", "note": "Draft computed from list prices. No payment provider is connected; nothing has been charged."}

    def save_invoice(self, org: str, month: str | None, actor: str) -> dict:
        d = self.invoice_data(org, month)
        iid = new_id("inv")
        self.db.execute("CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, period TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)")
        self.db.execute("INSERT INTO invoices(id,org_id,period,data,status,created_at) VALUES(?,?,?,?,?,?)", (iid, org, d["period"], json.dumps(d), "draft", time.time()))
        self.db.audit(actor, "invoice.drafted", None, org=org, invoice=iid, total=d["total"])
        return {"id": iid, **d}

    def invoices(self, org: str) -> list[dict]:
        self.db.execute("CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, period TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)")
        return [{"id": r["id"], "status": r["status"], **json.loads(r["data"])} for r in self.db.all("SELECT * FROM invoices WHERE org_id=? ORDER BY created_at DESC", (org,))]
