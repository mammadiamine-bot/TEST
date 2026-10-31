"""Controller side of Dedicated Worker Stations: registry, enrollment, secure token storage, health monitor, proxy."""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .db import new_id
from .netpolicy import NetworkPolicyError, assert_private_address
from .service import Core, CoreError
from .targetcore import TargetError
from .transports.agent import IdentityMismatch, WorkerClient, norm_fp
from .transports.base import TransportError

# Only these worker paths may be reached through the controller's proxy (the worker authorizes and validates again).
ALLOWED = re.compile(r"^/(status|clients(/[a-z0-9_-]+(/(secrets(/[A-Z0-9_]+)?|projects|fs/(list|read|write|delete)|exec|system-check|jobs/[0-9a-f]+(/cancel)?))?)?|tasks(/tsk_[0-9a-f]+(/(approve|cancel|pause|resume|retry|checkpoints))?)?|"
                     r"checkpoints/cp_[0-9a-f]+/restore|credentials(/cred_[0-9a-f]+)?|audit|firewall-script|unlock|emergency-stop)$")


POLL_OK = 10.0          # seconds between health probes while the Worker answers
BACKOFF_MAX = 60.0      # probe interval cap while it does not (10, 20, 40, 60 s ...): quick recovery, no hammering
PROBE_TIMEOUT = 5       # one probe never blocks longer than this (the normal client allows 15 s x 4 tries = ~1 min)
OFFLINE_AFTER = 3       # consecutive failed probes before the state is "offline" (1-2 failures = "unstable")

# error code -> why it happens and what to do (the dashboard translates the code)
HINTS = {
    "timeout": "No reply at all: the computer is asleep, switched off, out of Wi-Fi range or on another network. Wake it or check its power/network settings.",
    "refused": "The computer answers but the CIT Worker program is not running or not listening on that port. Start the Worker (or log on, if it starts at logon).",
    "identity": "The certificate is different from the one you confirmed. Do NOT connect until you verify it on the computer.",
    "auth": "The Worker rejected the saved credential (revoked, expired or the Worker data was reset). Enroll the computer again.",
    "unreachable": "No route to the computer: wrong address, or this PC is not on the same network/VPN.",
    "tls": "The connection started but the secure handshake failed. Another program may be using that port.",
    "locked": "The Worker is locked or its authorization window ended. Unlock it on the computer.",
    "other": "The Worker did not respond as expected.",
}


def classify(e: Exception) -> str:
    m = str(e).lower()
    if isinstance(e, IdentityMismatch):
        return "identity"
    if isinstance(e, TargetError):
        return "auth" if e.status in (401, 403) and "lock" not in m else ("locked" if "lock" in m or "authoriz" in m else "other")
    if "timed out" in m or "timeout" in m:
        return "timeout"
    if "refused" in m or "10061" in m:
        return "refused"
    if "no route" in m or "unreachable" in m or "10065" in m or "10051" in m or "getaddrinfo" in m or "name or service" in m:
        return "unreachable"
    if "ssl" in m or "tls" in m or "handshake" in m or "wrong version" in m:
        return "tls"
    return "other"


class Workers:
    def __init__(self, core: Core, monitor_interval: float = 10.0):
        self.listeners: list = []  # called as f(worker_id, online, status) after every health poll
        self.core, self.db = core, core.db
        self.clients: dict[str, WorkerClient] = {}
        self.state: dict[str, dict] = {}
        self.interval, self._stop = monitor_interval, threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cit-probe")
        self.polling: set[str] = set()
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
        fails = st.get("fails", 0)
        seen = st.get("seen") or r.get("last_seen")
        # online = the LAST probe succeeded (never faked). degraded = 1-2 failed probes in a row (unstable, still retried quickly).
        # needs_pairing = no controller credential. reconnecting = offline but seen in the last 10 minutes. else offline.
        if online:
            state = "online"
        elif not loaded:
            state = "needs_pairing"
        elif 0 < fails < OFFLINE_AFTER and st.get("seen") and time.time() - st["seen"] < 120:
            state = "degraded"
        else:
            state = "reconnecting" if seen and time.time() - seen < 600 else "offline"
        code = st.get("error_code")
        return {**r, "online": online, "state": state, "resources": st.get("status"), "credential_loaded": loaded, "last_seen": seen, "fails": fails,
                "error_code": None if online else code, "last_error": None if online else st.get("last_error"), "hint": None if online or not code else HINTS.get(code),
                "latency_ms": st.get("latency_ms"), "next_check_in": max(0, round(st.get("next_poll", 0) - time.time())) if st.get("next_poll") else None}

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
            self.db.audit("system", "worker.identity_mismatch", None, worker=wid); self._set(wid, False, error=e); raise CoreError(str(e), 403)
        except TransportError as e:
            self._set(wid, False, error=e); raise CoreError(f"worker unreachable: {e}", 502)
        except TargetError as e:
            if method != "GET" or e.status >= 500:
                self.db.audit(actor, "worker.call_failed", None, worker=wid, method=method, path=path.split("?")[0], status=e.status)
            raise CoreError(str(e), e.status)
        self._set(wid, True)
        if method != "GET":  # bodies (which may hold secrets) are never logged
            self.db.audit(actor, "worker.call", None, worker=wid, method=method, path=path.split("?")[0])
        return res

    # ---- health monitor with automatic reconnection
    def _set(self, wid: str, online: bool, status: dict | None = None, error: Exception | None = None, latency: float | None = None) -> None:
        st = self.state.setdefault(wid, {})
        prev_online, prev_state = st.get("online"), self.view(self.db.one("SELECT * FROM workers WHERE id=?", (wid,)) or {"id": wid}).get("state")
        now = time.time()
        if online:
            st.update(online=True, fails=0, status=status or st.get("status"), seen=now, error_code=None, last_error=None, latency_ms=latency, next_poll=now + POLL_OK)
            self.db.execute("UPDATE workers SET last_seen=?,status='online' WHERE id=? AND status IN ('enrolled','offline','online')", (now, wid))
        else:
            fails = st.get("fails", 0) + 1
            st.update(online=False, fails=fails, error_code=classify(error) if error else "other", last_error=str(error)[:300] if error else None, next_poll=now + min(POLL_OK * 2 ** (fails - 1), BACKOFF_MAX))
            if fails >= OFFLINE_AFTER:
                self.db.execute("UPDATE workers SET status='offline' WHERE id=? AND status IN ('enrolled','online')", (wid,))
        for f in self.listeners:
            try:
                f(wid, online, st.get("status"))
            except Exception:  # noqa: BLE001 - monitoring must never break the health poll
                pass
        new_state = self.view(self.db.one("SELECT * FROM workers WHERE id=?", (wid,)) or {"id": wid}).get("state")
        if prev_online is not None:
            if new_state == "online" and prev_state != "online":
                self.db.audit("system", "worker.reconnected", None, worker=wid)
            elif new_state in ("reconnecting", "offline") and prev_state in ("online", "degraded"):
                self.db.audit("system", "worker.offline", None, worker=wid, code=st.get("error_code"))

    def poll_one(self, wid: str) -> bool:
        c = self.clients.get(wid)
        if not c or not c.token:
            return False
        t0 = time.time()
        try:
            st = c.probe("/status", PROBE_TIMEOUT)
        except (TransportError, TargetError) as e:
            self._set(wid, False, error=e); return False
        self._set(wid, True, st, latency=round((time.time() - t0) * 1000)); return True

    def poll_once(self) -> dict:
        return {r["id"]: self.poll_one(r["id"]) for r in self.db.all("SELECT id FROM workers WHERE status IN ('enrolled','online','offline')")}

    def _poll_bg(self, wid: str) -> None:
        try:
            self.poll_one(wid)
        finally:
            self.polling.discard(wid)

    def tick(self) -> None:
        """Probe every worker whose time has come, in parallel, so one sleeping computer never delays the others."""
        now = time.time()
        for r in self.db.all("SELECT id FROM workers WHERE status IN ('enrolled','online','offline')"):
            wid = r["id"]
            if wid in self.polling or now < self.state.get(wid, {}).get("next_poll", 0):
                continue
            self.polling.add(wid); self.pool.submit(self._poll_bg, wid)

    def start_monitor(self) -> None:
        def loop():
            while not self._stop.wait(2.0):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def stop_monitor(self) -> None:
        self._stop.set()

    def reconnect(self, wid: str, actor: str = "technician") -> dict:
        """Operator pressed 'Reconnect': forget the back-off and probe right now. The state shown is the real result."""
        self.get(wid)
        self.state.setdefault(wid, {}).update(next_poll=0, fails=0)
        ok = self.poll_one(wid)
        self.db.audit(actor, "worker.reconnect_requested", None, worker=wid, ok=ok)
        return self.get(wid)

    def diagnose(self, wid: str, actor: str = "technician") -> dict:
        """Step-by-step connection test with timings. It reports what actually happened and why, and never changes anything."""
        w = self.get(wid)
        host, _, port = w["address"].partition(":")
        port = int(port or 8777)
        steps: list[dict] = []

        def step(name: str, fn):
            t0 = time.time()
            try:
                detail = fn(); steps.append({"step": name, "ok": True, "ms": round((time.time() - t0) * 1000), "detail": detail or ""}); return True
            except Exception as e:  # noqa: BLE001
                steps.append({"step": name, "ok": False, "ms": round((time.time() - t0) * 1000), "detail": str(e)[:200], "code": classify(e)}); return False

        verdict = "ok"
        c = self.clients.get(wid)
        ok = step("policy", lambda: assert_private_address(host) or "private address")
        ok = ok and step("tcp", lambda: socket.create_connection((host, port), timeout=4).close() or f"{host}:{port} accepts connections")
        if ok and c:
            from .transports.agent import _Pinned
            def tls():
                k = _Pinned(host, port, c.fp, 5)
                try:
                    k.connect()
                finally:
                    k.close()
                return "certificate matches the confirmed fingerprint"
            ok = step("tls", tls)
        if ok and c and c.token:
            ok = step("auth", lambda: c.probe("/status", PROBE_TIMEOUT) and "credential accepted")
        if ok and c and c.token:
            times = []
            for _ in range(3):
                t0 = time.time(); c.probe("/status", PROBE_TIMEOUT); times.append((time.time() - t0) * 1000)
            steps.append({"step": "latency", "ok": True, "ms": round(sum(times) / 3), "detail": f"avg {round(sum(times)/3)} ms, max {round(max(times))} ms over 3 requests"})
        if not c or not c.token:
            steps.append({"step": "auth", "ok": False, "ms": 0, "detail": "no controller credential for this computer", "code": "auth"})
        bad = next((x for x in steps if not x["ok"]), None)
        if bad:
            verdict = bad.get("code") or ("refused" if bad["step"] == "tcp" and "refused" in bad["detail"].lower() else "timeout" if bad["step"] == "tcp" else "other")
        self.db.audit(actor, "worker.diagnosed", None, worker=wid, verdict=verdict)
        return {"worker": wid, "state": w["state"], "steps": steps, "verdict": verdict, "hint": None if verdict == "ok" else HINTS.get(verdict), "last_seen": w["last_seen"]}

    def emergency_stop_all(self) -> None:
        for wid, c in list(self.clients.items()):
            try:
                c.call("POST", "/emergency-stop", {})
            except (TransportError, TargetError):
                self.db.audit("system", "worker.stop_failed", None, worker=wid)
