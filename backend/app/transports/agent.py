"""Client for the portable CIT agent. TLS with certificate pinning: the agent's fingerprint is shown on the
client's own screen, the owner reads it to the technician, and every connection is verified against it."""
from __future__ import annotations

import hashlib
import http.client
import json
import ssl
import time
from urllib.parse import quote

from ..targetcore import TargetError, b64d, b64e
from ..tools import Tool, console_script
from .base import Result, TransportError


def norm_fp(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").replace("-", "").lower()


class IdentityMismatch(TransportError):
    """The peer presented a different certificate than the one the owner confirmed. Never retried."""


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, host, port, fp, timeout):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # trust comes from the pin below, not from a CA
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._fp = fp

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        if not der or hashlib.sha256(der).hexdigest() != self._fp:
            self.close()
            raise IdentityMismatch("agent identity mismatch: certificate fingerprint differs from the one confirmed by the owner")


class AgentClient:
    kind = "agent"

    def __init__(self, host: str, port: int, fingerprint: str, token: str | None = None, timeout: int = 30):
        self.host, self.port, self.fp, self.token, self.timeout = host, port, norm_fp(fingerprint), token, timeout
        self.expires_at: float | None = None
        self.grants: list[str] = []

    # --- transport plumbing
    def _req(self, method: str, path: str, body: dict | None = None, auth: bool = True, timeout: int | None = None, retries: int = 0):
        delay = 0.3
        for attempt in range(retries + 1):
            try:
                return self._req_once(method, path, body, auth, timeout)
            except IdentityMismatch:
                raise
            except TransportError:
                if attempt == retries:
                    raise
                time.sleep(delay); delay *= 2  # exponential backoff for brief network interruptions

    def _req_once(self, method: str, path: str, body: dict | None = None, auth: bool = True, timeout: int | None = None):
        c = _Pinned(self.host, self.port, self.fp, timeout or self.timeout)
        try:
            h = {"Content-Type": "application/json"}
            if auth and self.token:
                h["Authorization"] = f"Bearer {self.token}"
            c.request(method, path, json.dumps(body) if body is not None else None, h)
            r = c.getresponse()
            raw = r.read()
        except (OSError, ssl.SSLError, http.client.HTTPException) as e:
            if isinstance(e, TransportError):
                raise
            raise TransportError(f"cannot reach agent at {self.host}:{self.port}: {e}") from e
        finally:
            c.close()
        data = json.loads(raw) if raw else {}
        if r.status >= 400:
            raise TargetError(data.get("detail", f"agent error {r.status}"), r.status)
        return data

    # --- pairing / lifecycle
    def pair(self, code: str, controller: str = "technician-station") -> dict:
        d = self._req("POST", "/pair", {"code": code, "controller": controller}, auth=False)
        self.token, self.expires_at, self.grants = d["token"], d["expires_at"], d["grants"]
        return d

    def info(self) -> dict:
        return self._req("GET", "/info")

    def disconnect(self) -> None:
        try:
            self._req("POST", "/disconnect", {})
        except (TargetError, TransportError):
            pass
        self.token = None

    # --- Target API
    def fs_list(self, path):
        return self._req("GET", f"/fs/list?path={quote(path)}")["entries"]

    def fs_read(self, path):
        return b64d(self._req("GET", f"/fs/read?path={quote(path)}")["content_b64"])

    def fs_write(self, path, data):
        self._req("PUT", "/fs/write", {"path": path, "content_b64": b64e(data)})

    def fs_delete(self, path):
        self._req("POST", "/fs/delete", {"path": path})

    def exec_start(self, argv, cwd, timeout):
        return self._req("POST", "/exec", {"argv": argv, "cwd": cwd, "timeout": timeout})["job"]

    def claude_start(self, prompt, cwd, permission_mode, allowed_tools, timeout):
        # Deliberately no env / credential parameter: the agent uses the CLIENT's own Claude login.
        return self._req("POST", "/claude", {"prompt": prompt, "cwd": cwd, "permission_mode": permission_mode,
                                              "allowed_tools": allowed_tools, "timeout": timeout})["job"]

    def job(self, jid, since=0):
        return self._req("GET", f"/jobs/{jid}?since={since}")

    def job_cancel(self, jid):
        return self._req("POST", f"/jobs/{jid}/cancel", {})

    # --- Transport API (existing diagnostics/repairs engine runs through the agent unchanged)
    def host_fingerprint(self) -> str:
        return self.fp

    def run(self, tool: Tool, params: dict, timeout_s: int) -> Result:
        t0 = time.time()
        d = self._req("POST", "/tool", {"tool_id": tool.id, "params": params}, timeout=timeout_s + 15)
        return Result(d["ok"], d["output"], d.get("error", ""), int((time.time() - t0) * 1000))

    def run_console(self, command: str, timeout_s: int) -> Result:
        console_script(command)  # validate locally too
        d = self._req("POST", "/console", {"command": command}, timeout=timeout_s + 15)
        return Result(d["ok"], d["output"], d.get("error", ""))


class WorkerClient(AgentClient):
    """Controller side of a Worker Station. Idempotent reads are retried with backoff; writes are never retried blindly."""
    kind = "worker"

    def enroll(self, code: str, controller: str, days: float | None = None) -> dict:
        d = self._req("POST", "/enroll", {"code": code, "controller": controller, **({"days": days} if days else {})}, auth=False)
        self.token = d["token"]
        return d

    def call(self, method: str, path: str, body: dict | None = None):
        return self._req(method, path, body, retries=3 if method == "GET" else 0)
