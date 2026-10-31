"""Live remote-desktop sessions through RustDesk (native viewer, opened on THIS computer).

Honest scope: RustDesk is a native application, so the live screen opens in its own window; CIT does not embed it in the browser.
What CIT adds around it: operator identity, tenant-scoped access, a recorded session with an expiry that CIT enforces by closing the
viewer it opened, early termination, emergency stop, and an audit trail. The computer being controlled shows RustDesk's own accept
prompt and session indicator; unattended access exists only if the owner set a permanent password in RustDesk on that computer
(CIT never sets or sends one). Windows login/lock screen and the UAC secure desktop are not bypassed (RustDesk itself needs an
installed service for those, which the owner controls).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

from .db import Db, new_id
from .service import CoreError

MAX_MINUTES = 240


def rustdesk_exe() -> str | None:
    return shutil.which("rustdesk") or next((p for p in (r"C:\Program Files\RustDesk\rustdesk.exe",) if os.path.exists(p)), None)


def _viewer_pids(remote_id: str) -> list[int]:
    """Processes of THIS computer's RustDesk that are connecting to that ID (the viewer windows CIT is allowed to close)."""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = p.info["cmdline"] or []
            if (p.info["name"] or "").lower().startswith("rustdesk") and remote_id in cl and "--connect" in cl:
                out.append(p.info["pid"])
        except Exception:  # noqa: BLE001
            continue
    return out


class Remote:
    def __init__(self, db: Db, popen=None, find_pids=None, kill=None, exe=None):
        self.db = db
        self.popen, self.find_pids, self.exe = popen or subprocess.Popen, find_pids or _viewer_pids, exe or rustdesk_exe
        self.kill = kill or self._kill
        self.timers: dict[str, threading.Timer] = {}
        self.pids: dict[str, list[int]] = {}

    @staticmethod
    def _kill(pid: int) -> None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)

    def start(self, org: str, wid: str, operator: str, minutes: int) -> dict:
        w = self.db.one("SELECT name,rustdesk_id FROM workers WHERE id=? AND org_id=?", (wid, org))
        if not w:
            raise CoreError("that computer does not belong to this client", 422)
        if not w["rustdesk_id"]:
            raise CoreError("save this computer's RustDesk ID first (it is shown in RustDesk on that computer)", 409)
        if not 1 <= minutes <= MAX_MINUTES:
            raise CoreError(f"session length must be 1..{MAX_MINUTES} minutes", 422)
        exe = self.exe()
        if not exe:
            raise CoreError("RustDesk is not installed on this computer (https://rustdesk.com)", 409)
        sid, now = new_id("rem"), time.time()
        self.db.execute("INSERT INTO remote_sessions(id,org_id,worker_id,operator,engine,started_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?)", (sid, org, wid, operator, "rustdesk", now, now + minutes * 60, "active"))
        self.popen([exe, "--connect", w["rustdesk_id"]], shell=False)   # no password is ever put on the command line
        time.sleep(0.2)
        pids = self.find_pids(w["rustdesk_id"])
        self.pids[sid] = pids
        self.db.execute("UPDATE remote_sessions SET controllable=? WHERE id=?", (1 if pids else 0, sid))
        t = threading.Timer(minutes * 60, self.end, args=(sid, "system", "expired")); t.daemon = True; t.start(); self.timers[sid] = t
        self.db.audit(operator, "remote.started", None, org=org, worker=wid, session=sid, minutes=minutes, engine="rustdesk")
        return self.get(sid)

    def end(self, sid: str, actor: str, reason: str = "ended") -> dict:
        s = self.db.one("SELECT * FROM remote_sessions WHERE id=?", (sid,))
        if not s:
            raise CoreError("unknown session", 404)
        if s["status"] != "active":
            return self.get(sid)
        for pid in set(self.pids.pop(sid, []) + (self.find_pids(self._rid(s["worker_id"])) if self._rid(s["worker_id"]) else [])):
            try:
                self.kill(pid)
            except Exception:  # noqa: BLE001
                pass
        t = self.timers.pop(sid, None)
        if t:
            t.cancel()
        self.db.execute("UPDATE remote_sessions SET status=?, ended_at=? WHERE id=?", (reason, time.time(), sid))
        self.db.audit(actor, "remote.ended", None, org=s["org_id"], worker=s["worker_id"], session=sid, reason=reason)
        return self.get(sid)

    def _rid(self, wid: str) -> str | None:
        r = self.db.one("SELECT rustdesk_id FROM workers WHERE id=?", (wid,))
        return r["rustdesk_id"] if r else None

    def get(self, sid: str) -> dict:
        return self.db.one("SELECT * FROM remote_sessions WHERE id=?", (sid,))

    def list(self, wid: str) -> list[dict]:
        return self.db.all("SELECT * FROM remote_sessions WHERE worker_id=? ORDER BY started_at DESC LIMIT 50", (wid,))

    def end_all(self) -> None:
        for s in self.db.all("SELECT id FROM remote_sessions WHERE status='active'"):
            self.end(s["id"], "system", "emergency_stop")

    def sweep(self) -> None:
        """Called by the app periodically: close sessions whose time is up even if the timer thread was lost (e.g. after a restart)."""
        for s in self.db.all("SELECT id FROM remote_sessions WHERE status='active' AND expires_at<?", (time.time(),)):
            self.end(s["id"], "system", "expired")
