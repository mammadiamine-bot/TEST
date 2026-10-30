#!/usr/bin/env python3
"""Claude IT Technician — portable client agent.

Runs ONLY while this window is open. Nothing is installed, no service or startup entry is created.
The machine's owner starts it, reads the pairing code and fingerprint to the technician, and chooses
what is allowed. Type `d` + Enter (or close the window / Ctrl+C) to disconnect immediately.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
for cand in (HERE.parent / "backend", HERE / "backend"):  # dev checkout / portable bundle
    if (cand / "app").is_dir():
        sys.path.insert(0, str(cand))

from app import tools  # noqa: E402
from app.netpolicy import _ok as _private_ip  # noqa: E402
from app.targetcore import (FsGuard, Grants, JobManager, TargetError, b64d, b64e, claude_argv, detect_tools,  # noqa: E402
                            redact_argv)

import ipaddress  # noqa: E402

VERSION = "0.2.0"
ALLOWED_FIELDS = {
    "/pair": {"code", "controller"}, "/exec": {"argv", "cwd", "timeout"},
    "/claude": {"prompt", "cwd", "permission_mode", "allowed_tools", "timeout"},  # no env/credential field exists
    "/fs/write": {"path", "content_b64"}, "/fs/delete": {"path"}, "/tool": {"tool_id", "params"}, "/console": {"command"},
    "/disconnect": set(),
}


def make_cert(dirpath: Path) -> tuple[Path, Path, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cit-agent")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    cp, kp = dirpath / "c.pem", dirpath / "k.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp, cert.fingerprint(hashes.SHA256()).hex()


class Agent:
    def __init__(self, roots, grants: Grants, minutes: int, say=print, ps_runner=None, pair_ttl=600):
        self.say, self.grants, self.minutes = say, grants, minutes
        self.guard = FsGuard(roots) if roots else None
        self.jobs = JobManager(self.guard) if self.guard else None
        self.code = f"{secrets.randbelow(10**8):08d}"
        self.pair_expires = time.time() + pair_ttl
        self.attempts = 0
        self.token: str | None = None
        self.expires_at: float | None = None
        self.controller = ""
        self.ended = threading.Event()
        self.end_reason = ""
        self.ps_runner = ps_runner or self._powershell
        self.lock = threading.Lock()

    # ---- lifecycle
    def end(self, reason: str) -> None:
        with self.lock:
            if self.ended.is_set():
                return
            self.end_reason = reason
            self.token = None
            n = self.jobs.cancel_all() if self.jobs else 0
            self.say(f"[{time.strftime('%H:%M:%S')}] SESSION ENDED: {reason}" + (f" ({n} running task(s) stopped)" if n else ""))
            self.ended.set()

    def authed(self, header: str) -> bool:
        if self.token is None or self.expires_at is None:
            return False
        if time.time() > self.expires_at:
            self.end("session expired")
            return False
        scheme, _, tok = header.partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(tok, self.token)

    # ---- operations
    def need(self, g: str) -> None:
        if not getattr(self.grants, g):
            raise TargetError(f"'{g}' was not allowed by the owner of this computer", 403)

    def info(self) -> dict:
        import platform
        return {"agent": VERSION, "os": platform.system().lower(), "host": platform.node(), "roots": [str(r) for r in (self.guard.roots if self.guard else [])],
                "grants": self.grants.names(), "expires_at": self.expires_at, "tools": detect_tools()}

    def _fs(self) -> FsGuard:
        if not self.guard:
            raise TargetError("no project directory was authorized (start the agent with --root)", 403)
        return self.guard

    def _powershell(self, script: str, timeout: int):
        import base64
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if os.name != "nt" or not exe:
            raise TargetError("PowerShell tools are only available on Windows", 501)
        p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout, p.stderr[-2000:]

    def run_tool(self, tool_id: str, params: dict) -> dict:
        t = tools.get(tool_id)
        if t.kind == "repair":
            self.need("repair")
        elif t.kind != "diagnostic":
            raise TargetError("tool not available through the agent", 403)
        ok, out, err = self.ps_runner(t.render(t.validate(params)), t.timeout_s)
        self.say(f"[{time.strftime('%H:%M:%S')}] tool {t.id} ({t.risk.value}) -> {'ok' if ok else 'failed'}")
        return {"ok": ok, "output": out, "error": err}

    def run_console(self, command: str) -> dict:
        ok, out, err = self.ps_runner(tools.console_script(command), 30)
        self.say(f"[{time.strftime('%H:%M:%S')}] console: {command}")
        return {"ok": ok, "output": out[:20000], "error": err}


def make_handler(ag: Agent):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "cit-agent"

        def log_message(self, *a):  # silence default access log
            pass

        def _send(self, code: int, obj: dict) -> None:
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def _body(self, route: str) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 8 * 1024 * 1024:
                raise TargetError("body too large", 413)
            d = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(d, dict) or set(d) - ALLOWED_FIELDS.get(route, set()):
                raise TargetError(f"unexpected fields: {sorted(set(d) - ALLOWED_FIELDS.get(route, set())) if isinstance(d, dict) else 'body'}")
            return d

        def _handle(self, method: str) -> None:
            try:
                ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
                if not _private_ip(ip):
                    return self._send(403, {"detail": "connections from public addresses are refused"})
                u = urlparse(self.path); route = u.path; q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if route == "/pair" and method == "POST":
                    return self._pair(self._body(route))
                if not ag.authed(self.headers.get("Authorization", "")):
                    return self._send(401, {"detail": "not paired, or session ended"})
                self._route(method, route, q)
            except TargetError as e:
                self._send(e.status, {"detail": str(e)})
            except tools.ToolError as e:
                self._send(422, {"detail": str(e)})
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                self._send(400, {"detail": f"bad request: {e}"})
            except subprocess.TimeoutExpired:
                self._send(504, {"detail": "timed out"})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"detail": f"agent error: {type(e).__name__}"})

        def _pair(self, d: dict) -> None:
            with ag.lock:
                if ag.token is not None:
                    return self._send(409, {"detail": "already paired"})
                ag.attempts += 1
                if ag.attempts > 5:
                    threading.Thread(target=ag.end, args=("too many wrong pairing codes",), daemon=True).start()
                    return self._send(429, {"detail": "locked"})
                if time.time() > ag.pair_expires:
                    return self._send(410, {"detail": "pairing code expired"})
                if not secrets.compare_digest(str(d.get("code", "")), ag.code):
                    ag.say(f"[{time.strftime('%H:%M:%S')}] WRONG pairing code attempt {ag.attempts}/5")
                    return self._send(403, {"detail": "wrong pairing code"})
                ag.token = secrets.token_urlsafe(32)
                ag.expires_at = time.time() + ag.minutes * 60
                ag.controller = str(d.get("controller", ""))[:60]
            ag.say(f"[{time.strftime('%H:%M:%S')}] PAIRED with '{ag.controller}'. Session ends at {time.strftime('%H:%M:%S', time.localtime(ag.expires_at))}. Type d + Enter to disconnect now.")
            self._send(200, {"token": ag.token, "expires_at": ag.expires_at, "grants": ag.grants.names()})

        def _route(self, method: str, route: str, q: dict) -> None:
            now = time.strftime("%H:%M:%S")
            if route == "/info" and method == "GET":
                return self._send(200, ag.info())
            if route == "/disconnect" and method == "POST":
                self._body(route); self._send(200, {"ok": True})
                return ag.end(f"disconnected by '{ag.controller}'")
            if route == "/fs/list" and method == "GET":
                return self._send(200, {"entries": ag._fs().list(q.get("path", "."))})
            if route == "/fs/read" and method == "GET":
                ag.say(f"[{now}] read {q.get('path')}")
                return self._send(200, {"content_b64": b64e(ag._fs().read(q.get("path", "")))})
            if route == "/fs/write" and method == "PUT":
                ag.need("write"); d = self._body(route)
                ag._fs().write(d["path"], b64d(d["content_b64"])); ag.say(f"[{now}] WRITE {d['path']}")
                return self._send(200, {"ok": True})
            if route == "/fs/delete" and method == "POST":
                ag.need("write"); d = self._body(route)
                ag._fs().delete(d["path"]); ag.say(f"[{now}] DELETE {d['path']}")
                return self._send(200, {"ok": True})
            if route == "/exec" and method == "POST":
                ag.need("terminal"); d = self._body(route)
                argv = d["argv"]
                ag._fs()
                job = ag.jobs.start(argv, d.get("cwd"), int(d.get("timeout", 120)))
                ag.say(f"[{now}] EXEC {' '.join(redact_argv(argv))[:200]}")
                return self._send(200, {"job": job.id})
            if route == "/claude" and method == "POST":
                ag.need("claude"); d = self._body(route)
                argv = claude_argv(d["prompt"], d.get("permission_mode", "default"), d.get("allowed_tools", []))
                ag._fs()
                job = ag.jobs.start(argv, d.get("cwd"), int(d.get("timeout", 1800)), scrub=False)  # client's own login/env
                ag.say(f"[{now}] CLAUDE CODE task started in {d.get('cwd')} (mode={d.get('permission_mode', 'default')})")
                return self._send(200, {"job": job.id})
            if route.startswith("/jobs/") and ag.jobs:
                parts = route.split("/")
                if method == "GET" and len(parts) == 3:
                    return self._send(200, ag.jobs.get(parts[2]).snapshot(int(q.get("since", 0))))
                if method == "POST" and len(parts) == 4 and parts[3] == "cancel":
                    ag.say(f"[{now}] job {parts[2]} cancelled")
                    return self._send(200, ag.jobs.cancel(parts[2]).snapshot())
            if route == "/tool" and method == "POST":
                d = self._body(route)
                return self._send(200, ag.run_tool(d["tool_id"], d.get("params") or {}))
            if route == "/console" and method == "POST":
                return self._send(200, ag.run_console(self._body(route)["command"]))
            self._send(404, {"detail": "unknown route"})

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PUT(self): self._handle("PUT")

    return H


def serve(ag: Agent, bind: str, port: int):
    """Returns (server, fingerprint). Cert and key exist on disk only for the instant it takes to load them."""
    tmp = Path(tempfile.mkdtemp(prefix="cit-agent-"))
    try:
        cp, kp, fp = make_cert(tmp)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cp, kp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    class Quiet(ThreadingHTTPServer):
        def handle_error(self, request, client_address):  # aborted/pin-rejected handshakes are expected, not errors
            pass

    srv = Quiet((bind, port), make_handler(ag))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, fp


def private_addresses() -> list[str]:
    """This computer's own LAN addresses, shown on its screen so nobody has to scan the network to find it."""
    found: list[str] = []
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and _private_ip(ipaddress.ip_address(a.address)) and not a.address.startswith("127."):
                    found.append(f"{a.address} ({name})")
    except ImportError:
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if _private_ip(ipaddress.ip_address(ip)) and not ip.startswith("127."):
                    found.append(ip)
        except OSError:
            pass
    return found


def watchdog(ag: Agent):
    while not ag.ended.wait(1):
        if ag.expires_at and time.time() > ag.expires_at:
            ag.end("session expired")
        elif ag.token is None and time.time() > ag.pair_expires + 60:
            ag.end("nobody paired in time")


def selftest() -> int:
    from app.transports.agent import AgentClient
    root = Path(tempfile.mkdtemp(prefix="cit-selftest-"))
    try:
        (root / "a.txt").write_text("hi")
        ag = Agent([root], Grants(), 5, say=lambda *_: None)
        srv, fp = serve(ag, "127.0.0.1", 0)
        port = srv.server_address[1]
        bad = AgentClient("127.0.0.1", port, "00" * 32)
        try:
            bad.pair(ag.code); return fail("wrong fingerprint was accepted")
        except Exception:
            pass
        c = AgentClient("127.0.0.1", port, fp)
        try:
            c.pair("00000000"); return fail("wrong code accepted")
        except TargetError:
            pass
        c.pair(ag.code)
        assert c.fs_read(str(root / "a.txt")) == b"hi"
        for f in (lambda: c.exec_start([sys.executable, "-V"], None, 5), lambda: c.fs_write(str(root / "b"), b"x")):
            try:
                f(); return fail("ungranted capability was allowed")
            except TargetError as e:
                assert e.status == 403
        c.disconnect(); assert ag.ended.is_set()
        srv.shutdown()
        print("selftest OK: TLS pinning, pairing, confinement, default-deny grants, disconnect")
        return 0
    except Exception as e:  # noqa: BLE001
        return fail(repr(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def fail(msg: str) -> int:
    print("selftest FAILED:", msg); return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", action="append", default=[], help="project directory the technician may work in (repeatable)")
    p.add_argument("--allow-terminal", action="store_true", help="allow running commands (argv, no shell) inside --root")
    p.add_argument("--allow-write", action="store_true", help="allow creating/changing/deleting files inside --root")
    p.add_argument("--allow-repair", action="store_true", help="allow Windows repair tools (service restarts, cleanup, ...)")
    p.add_argument("--allow-claude", action="store_true", help="allow Claude Code to run here using THIS computer's own Claude login")
    p.add_argument("--minutes", type=int, default=30, help="session length, 1-240")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--bind", default="0.0.0.0", help="interface (connections from public IPs are refused anyway)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()
    if not 1 <= a.minutes <= 240:
        p.error("--minutes must be 1..240")
    for r in a.root:
        if not Path(r).is_dir():
            p.error(f"--root {r!r} is not a directory")
    ag = Agent(a.root, Grants(a.allow_terminal, a.allow_write, a.allow_repair, a.allow_claude), a.minutes)
    srv, fp = serve(ag, a.bind, a.port)
    spaced = " ".join(fp[i:i + 8] for i in range(0, len(fp), 8)).upper()
    print(f"\n=== Claude IT Technician agent {VERSION} ===\n"
          f"Computer:     {socket.gethostname()}\nAddresses:    {', '.join(private_addresses()) or '(none found: check Wi-Fi)'}\n"
          f"Port:         {a.port}\nPairing code: {ag.code}   (valid 10 min, single use)\nFingerprint:  {spaced}\n"
          f"Allowed:      read-only diagnostics" + "".join(f", {n}" for n in ag.grants.names()) +
          f"\nProject dirs: {', '.join(a.root) or '(none)'}\nSession:      {a.minutes} min after pairing\n"
          "Read the code AND the fingerprint to your technician. They must match on their screen.\n"
          "Type  d  + Enter at any time to disconnect immediately.\n")
    threading.Thread(target=watchdog, args=(ag,), daemon=True).start()
    if sys.stdin and sys.stdin.isatty():
        def reader():
            for line in sys.stdin:
                if line.strip().lower() in ("d", "disconnect", "q", "quit"):
                    ag.end("disconnected by the computer's owner"); return
        threading.Thread(target=reader, daemon=True).start()
    try:
        ag.ended.wait()
    except KeyboardInterrupt:
        ag.end("interrupted by the computer's owner")
    srv.shutdown(); srv.server_close()
    print("Agent stopped. Nothing was installed and no temporary files remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
