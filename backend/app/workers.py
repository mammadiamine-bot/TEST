"""Controller side of Dedicated Worker Stations: registry, enrollment, secure token storage, health monitor, proxy."""
from __future__ import annotations

import json
import re
import threading
import time

from .db import new_id
from .netpolicy import NetworkPolicyError, assert_private_address
from .service import Core, CoreError
from .targetcore import TargetError
from .transports.agent import IdentityMismatch, WorkerClient, norm_fp
from .transports.base import TransportError

# Only these worker paths may be reached through the controller's proxy (the worker authorizes and validates again).
ALLOWED = re.compile(r"^/(status|clients(/[a-z0-9_-]+(/(secrets(/[A-Z0-9_]+)?|projects|fs/(list|read|write|delete)|exec|system-check|jobs/[0-9a-f]+(/cancel)?))?)?|tasks(/tsk_[0-9a-f]+(/(approve|cancel|pause|resume|retry|checkpoints))?)?|"
                     r"checkpoints/cp_[0-9a-f]+/restore|credentials(/cred_[0-9a-f]+)?|audit|firewall-script|unlock|emergency-stop)$")


class Workers:
    def __init__(self, core: Core, monitor_interval: float = 10.0):
        self.listeners: list = []  # called as f(worker_id, online, status) after every health poll
        self.core, self.db = core, core.db
        self.clients: dict[str, WorkerClient] = {}
        self.state: dict[str, dict] = {}
        self.interval, self._stop = monitor_interval, threading.Event()
        core.stop_hooks.append(self.emergency_stop_all)
        for r in self.db.all("SELECT id,address,fingerprint FROM workers WHERE status IN ('enrolled','offline','online')"):
            blob = self.db.one("SELECT blob FROM credentials WHERE device_id=?", (f"worker:{r['id']}",))
            if blob:
                try:
                    tok = core.protector.unprotect(blob["blob"]).decode()
                    self._mk(r["id"], r["address"], r["fingerprint"], tok)
                except Exception:  # noqa: BLE001  (protector unavailable on this platform)
                    pass

    # ---- registry
    def _mk(self, wid: str, address: str, fp: str, token: str | None = None) -> WorkerClient:
        host, _, port = address.partition(":")
        c = WorkerClient(host, int(port or 8777), fp, token, timeout=15)
        self.clients[wid] = c
        return c

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM workers ORDER BY created_at"):
            out.append(self.view(r))
        return out

    def view(self, r: dict) -> dict:
        st = self.state.get(r["id"], {})
        loaded = r["id"] in self.clients and bool(self.clients[r["id"]].token)
        online = st.get("online", False)
        seen = st.get("seen") or r.get("last_seen")
        # needs_pairing: no controller credential; reconnecting: offline but seen within 10 min (the monitor keeps retrying); else offline
        state = "online" if online else ("needs_pairing" if not loaded else ("reconnecting" if seen and time.time() - seen < 600 else "offline"))
        return {**r, "online": online, "state": state, "resources": st.get("status"), "credential_loaded": loaded}

    def get(self, wid: str) -> dict:
        r = self.db.one("SELECT * FROM workers WHERE id=?", (wid,))
        if not r:
            raise CoreError("worker not found", 404)
        return self.view(r)

    def add(self, name: str, address: str, fingerprint: str, rustdesk_id: str | None = None, notes: str = "") -> dict:
        host = address.rsplit(":", 1)[0] if re.search(r":\d+$", address) else address
        try:
            assert_private_address(host)  # LAN, Ethernet or Tailscale only. Never a public address.
        except NetworkPolicyError as e:
            self.db.audit("technician", "worker.rejected", None, address=address, reason=str(e)); raise CoreError(str(e), 422)
        fp = norm_fp(fingerprint)
        if len(fp) != 64:
            raise CoreError("fingerprint must be the 64 hex characters shown on the worker's console", 422)
        if rustdesk_id and not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", rustdesk_id):
            raise CoreError("invalid RustDesk ID", 422)
        wid = new_id("wst")
        self.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,rustdesk_id,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (wid, name[:100], address if re.search(r":\d+$", address) else address + ":8777", fp, "pending_enrollment", rustdesk_id, notes[:500], time.time()))
        self.db.audit("technician", "worker.added", None, worker=wid, address=address)
        return self.get(wid)

    def update(self, wid: str, rustdesk_id: str | None, notes: str | None) -> dict:
        self.get(wid)
        if rustdesk_id is not None:
            if rustdesk_id and not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", rustdesk_id):
                raise CoreError("invalid RustDesk ID", 422)
            self.db.execute("UPDATE workers SET rustdesk_id=? WHERE id=?", (rustdesk_id or None, wid))
        if notes is not None:
            self.db.execute("UPDATE workers SET notes=? WHERE id=?", (notes[:500], wid))
        return self.get(wid)

    def enroll(self, wid: str, code: str, days: float | None = None) -> dict:
        w = self.get(wid)
        if w["status"] not in ("pending_enrollment",):
            raise CoreError(f"worker is {w['status']}", 409)
        c = self._mk(wid, w["address"], w["fingerprint"])
        try:
            d = c.enroll(code, "technician-laptop", days)
        except TargetError as e:
            self.db.audit("technician", "worker.enroll_failed", None, worker=wid, reason=str(e)); self.clients.pop(wid, None); raise CoreError(str(e), e.status)
        except TransportError as e:
            self.clients.pop(wid, None); raise CoreError(str(e), 502)
        persisted = True
        try:  # the long-lived credential is stored with Windows DPAPI; elsewhere it stays in memory only
            self.db.execute("INSERT OR REPLACE INTO credentials(device_id,blob) VALUES(?,?)", (f"worker:{wid}", self.core.protector.protect(d["token"].encode())))
        except Exception:  # noqa: BLE001
            persisted = False
        self.db.execute("UPDATE workers SET status='enrolled',worker_id=?,credential_id=? WHERE id=?", (d["worker_id"], d["credential_id"], wid))
        self.db.audit("technician", "worker.enrolled", None, worker=wid, credential=d["credential_id"], token_persisted=persisted)
        self.poll_one(wid)
        out = self.get(wid); out["token_persisted"] = persisted
        return out

    def remove(self, wid: str, revoke: bool = True) -> None:
        w = self.get(wid)
        if revoke and wid in self.clients and w["credential_id"]:
            try:
                self.clients[wid].call("DELETE", f"/credentials/{w['credential_id']}")
            except (TargetError, TransportError):
                pass
        self.clients.pop(wid, None); self.state.pop(wid, None)
        self.db.execute("DELETE FROM credentials WHERE device_id=?", (f"worker:{wid}",))
        self.db.execute("DELETE FROM workers WHERE id=?", (wid,))
        self.db.audit("technician", "worker.removed", None, worker=wid, revoked=revoke)

    # ---- calls
    def call(self, wid: str, method: str, path: str, body: dict | None = None, actor="technician"):
        if not ALLOWED.match(path.split("?")[0]):
            raise CoreError("path is not available through the controller", 403)
        w = self.get(wid)
        c = self.clients.get(wid)
        if not c or not c.token:
            raise CoreError("no credential for this worker (enroll again)", 409)
        try:
            res = c.call(method, path, body)
        except IdentityMismatch as e:
            self.db.audit("system", "worker.identity_mismatch", None, worker=wid); self._set(wid, False); raise CoreError(str(e), 403)
        except TransportError as e:
            self._set(wid, False); raise CoreError(f"worker unreachable: {e}", 502)
        except TargetError as e:
            if method != "GET" or e.status >= 500:
                self.db.audit(actor, "worker.call_failed", None, worker=wid, method=method, path=path.split("?")[0], status=e.status)
            raise CoreError(str(e), e.status)
        self._set(wid, True)
        if method != "GET":  # bodies (which may hold secrets) are never logged
            self.db.audit(actor, "worker.call", None, worker=wid, method=method, path=path.split("?")[0])
        return res

    # ---- health monitor with automatic reconnection
    def _set(self, wid: str, online: bool, status: dict | None = None) -> None:
        prev = self.state.get(wid, {}).get("online")
        self.state[wid] = {"online": online, "status": status or self.state.get(wid, {}).get("status"), "seen": time.time() if online else self.state.get(wid, {}).get("seen")}
        if online:
            self.db.execute("UPDATE workers SET last_seen=?,status='online' WHERE id=? AND status IN ('enrolled','offline','online')", (time.time(), wid))
        else:
            self.db.execute("UPDATE workers SET status='offline' WHERE id=? AND status IN ('enrolled','online')", (wid,))
        for f in self.listeners:
            try:
                f(wid, online, self.state[wid]['status'])
            except Exception:  # noqa: BLE001 - monitoring must never break the health poll
                pass
        if prev is not None and prev != online:
            self.db.audit("system", "worker.reconnected" if online else "worker.offline", None, worker=wid)

    def poll_one(self, wid: str) -> bool:
        c = self.clients.get(wid)
        if not c or not c.token:
            return False
        try:
            st = c.call("GET", "/status")
        except (TransportError, TargetError):
            self._set(wid, False); return False
        self._set(wid, True, st); return True

    def poll_once(self) -> dict:
        return {r["id"]: self.poll_one(r["id"]) for r in self.db.all("SELECT id FROM workers WHERE status IN ('enrolled','online','offline')")}

    def start_monitor(self) -> None:
        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def stop_monitor(self) -> None:
        self._stop.set()

    def emergency_stop_all(self) -> None:
        for wid, c in list(self.clients.items()):
            try:
                c.call("POST", "/emergency-stop", {})
            except (TransportError, TargetError):
                self.db.audit("system", "worker.stop_failed", None, worker=wid)
