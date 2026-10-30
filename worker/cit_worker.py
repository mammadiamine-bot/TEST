#!/usr/bin/env python3
"""Claude IT Technician — Dedicated Worker Station.

A Windows (or Linux) computer you own, left at an authorized client site. It runs Claude Code and development jobs
for several clients at once, isolated from each other, and keeps working if your laptop disconnects.

Explicit, visible and revocable:  enrolled by a code shown on THIS machine · TLS with a pinned fingerprint ·
authorization window (`authorize`) after which it locks itself · `lock` / STOP file / `wipe` for the site's staff ·
installed as a named scheduled task only when you ask (`service install`). No hidden persistence.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
for cand in (HERE.parent / "backend", HERE / "backend"):
    if (cand / "app").is_dir():
        sys.path.insert(0, str(cand))

from app.claudeutil import claude_event, clean  # noqa: E402
from app.netpolicy import _ok as _private_ip  # noqa: E402
from app.protect import DpapiProtector, UnavailableProtector  # noqa: E402
from app.targetcore import (SKIP_DIRS, FsGuard, JobManager, TargetError, b64d, b64e, claude_argv, detect_tools,  # noqa: E402
                            redact_argv)

VERSION = "0.1.0"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,60}$")
GIT_URL = re.compile(r"^(https://[\w.-]+/[\w./~%-]+|git@[\w.-]+:[\w./~-]+|ssh://[\w.@-]+/[\w./~-]+|file:///[\w./-]+)$")
HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
CLAUDE_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"]
KINDS = {"claude", "command", "git_clone", "git_pull", "deploy"}
DESTRUCTIVE = {"command", "deploy"}  # never re-run automatically after an interruption
MAX_CHECKPOINT = 100 * 1024 * 1024
FINAL = {"completed", "completed_unverified", "failed", "cancelled"}


# ------------------------------------------------------------------ secret protection
class FileKeyProtector:
    """DEV ONLY (non-Windows): Fernet key in a 0600 file next to the data. NOT OS-backed; status reports it."""
    os_backed = False

    def __init__(self, data_dir: Path):
        from cryptography.fernet import Fernet
        data_dir.mkdir(parents=True, exist_ok=True)
        kf = data_dir / "dev.key"
        if not kf.exists():
            kf.write_bytes(Fernet.generate_key()); os.chmod(kf, 0o600)
        self.f = Fernet(kf.read_bytes())

    def protect(self, d: bytes) -> bytes:
        return self.f.encrypt(d)

    def unprotect(self, b: bytes) -> bytes:
        return self.f.decrypt(b)


def make_protector(data_dir: Path, dev_store: bool):
    if sys.platform == "win32":
        p = DpapiProtector(); p.os_backed = True; return p
    if dev_store:
        return FileKeyProtector(data_dir)
    p = UnavailableProtector(); p.os_backed = False; return p


# ------------------------------------------------------------------ persistent store
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, controller TEXT NOT NULL, created REAL NOT NULL, expires REAL, revoked INTEGER NOT NULL DEFAULT 0, last_used REAL);
CREATE TABLE IF NOT EXISTS enroll(code_hash TEXT PRIMARY KEY, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY, name TEXT NOT NULL, systems TEXT NOT NULL, authorized_until REAL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects(client_id TEXT NOT NULL, name TEXT NOT NULL, build_cmd TEXT, test_cmd TEXT, deploy_cmd TEXT, env_secrets TEXT NOT NULL, git_url TEXT, created REAL NOT NULL, PRIMARY KEY(client_id,name));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, client_id TEXT NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, ts REAL NOT NULL, level TEXT NOT NULL, msg TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, client_id TEXT NOT NULL, project TEXT NOT NULL, path TEXT NOT NULL, created REAL NOT NULL, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        self.c = sqlite3.connect(str(path), check_same_thread=False); self.c.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.c.executescript(SCHEMA)

    def x(self, sql, a=()):
        with self.lock:
            cur = self.c.execute(sql, a); self.c.commit(); return cur

    def one(self, sql, a=()):
        with self.lock:
            r = self.c.execute(sql, a).fetchone()
        return dict(r) if r else None

    def all(self, sql, a=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, a).fetchall()]

    def kv(self, k, default=None):
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def setkv(self, k, v):
        self.x("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))

    def audit(self, actor, action, **detail):
        d = json.dumps(detail, sort_keys=True, default=str)
        with self.lock:
            r = self.c.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = r["hash"] if r else "0" * 64
            ts = time.time()
            h = hashlib.sha256(f"{prev}|{ts!r}|{actor}|{action}|{d}".encode()).hexdigest()
            self.c.execute("INSERT INTO audit(ts,actor,action,detail,prev,hash) VALUES(?,?,?,?,?,?)", (ts, actor, action, d, prev, h)); self.c.commit()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ------------------------------------------------------------------ the worker
class Worker:
    def __init__(self, data_dir: Path, protector, say=print, auto_resume=False, max_parallel=1, tick=0.2, allow_terminal=True):
        self.dir = Path(data_dir); self.dir.mkdir(parents=True, exist_ok=True)
        for sub in ("workspaces", "secrets", "checkpoints"):
            (self.dir / sub).mkdir(exist_ok=True)
        self.store = Store(self.dir / "state.sqlite3")
        self.prot, self.say, self.auto_resume, self.tick, self.allow_terminal = protector, say, auto_resume, tick, allow_terminal
        self.slots = threading.Semaphore(max_parallel)
        self.jms: dict[str, JobManager] = {}
        self.cancel_flags: set[str] = set()
        self.running: dict[str, threading.Thread] = {}
        self.stop = threading.Event()
        ident = self.store.kv("identity")
        if not ident:
            ident = {"worker_id": "wrk_" + secrets.token_hex(5), "name": os.environ.get("COMPUTERNAME") or "worker"}
            self.store.setkv("identity", ident)
        self.id, self.name = ident["worker_id"], ident["name"]
        self.recover()
        self.sched = threading.Thread(target=self._scheduler, daemon=True)

    # ---- identity / cert
    def ensure_cert(self) -> tuple[Path, str]:
        cp, kp = self.dir / "cert.pem", self.dir / "key.enc"
        if not cp.exists() or not kp.exists():
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
            import datetime
            key = ec.generate_private_key(ec.SECP256R1())
            nm = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.id)])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(nm).issuer_name(nm).public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=730)).sign(key, hashes.SHA256()))
            kp.write_bytes(self.prot.protect(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))
            cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            self.store.audit("system", "identity.created", worker=self.id)
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        return cp, x509.load_pem_x509_certificate(cp.read_bytes()).fingerprint(hashes.SHA256()).hex()

    def ssl_context(self) -> tuple[ssl.SSLContext, str]:
        cp, fp = self.ensure_cert()
        tmp = Path(tempfile.mkdtemp(prefix="cit-w-"))
        try:
            kf = tmp / "k.pem"; kf.write_bytes(self.prot.unprotect((self.dir / "key.enc").read_bytes()))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cp, kf)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ctx, fp

    # ---- authorization state
    def authorized_until(self):
        return self.store.kv("authorized_until")

    def locked(self) -> str | None:
        return self.store.kv("locked")

    def lock(self, reason: str, actor="system") -> None:
        self.store.setkv("locked", reason)
        n = self.cancel_everything()
        self.store.audit(actor, "worker.locked", reason=reason, jobs_cancelled=n)
        self.say(f"[{time.strftime('%H:%M:%S')}] LOCKED: {reason} ({n} job(s) stopped)")

    def unlock(self, actor) -> None:
        until = self.authorized_until()
        if until and time.time() > until:
            raise TargetError("authorization window has expired; extend it on this machine with `authorize`", 403)
        self.store.x("DELETE FROM kv WHERE k='locked'")
        self.store.audit(actor, "worker.unlocked")

    def authorize(self, days: float) -> float:
        until = time.time() + days * 86400
        self.store.setkv("authorized_until", until); self.store.audit("local-owner", "authorization.set", until=until)
        return until

    def need_ready(self) -> None:
        r = self.locked()
        if r:
            raise TargetError(f"worker is locked: {r}", 423)
        until = self.authorized_until()
        if until and time.time() > until:
            self.lock("authorization window expired")
            raise TargetError("worker is locked: authorization window expired", 423)

    def cancel_everything(self) -> int:
        n = sum(jm.cancel_all() for jm in self.jms.values())
        for t in self.store.all("SELECT id FROM tasks WHERE status IN ('running','queued')"):
            self.cancel_flags.add(t["id"])
        return n

    # ---- enrollment / credentials
    def new_enroll_code(self) -> str:
        code = f"{secrets.randbelow(10**8):08d}"
        self.store.x("DELETE FROM enroll"); self.store.x("INSERT INTO enroll(code_hash,expires) VALUES(?,?)", (sha(code), time.time() + 600))
        self.store.audit("local-owner", "enroll.code_issued")
        return code

    def enroll(self, code: str, controller: str, days: float | None) -> dict:
        row = self.store.one("SELECT * FROM enroll")
        if not row or time.time() > row["expires"]:
            raise TargetError("no valid enrollment code: run `enroll` on this machine", 410)
        self.store.x("UPDATE enroll SET attempts=attempts+1")
        if row["attempts"] >= 5:
            self.store.x("DELETE FROM enroll"); raise TargetError("too many attempts; code cancelled", 429)
        if not secrets.compare_digest(sha(code), row["code_hash"]):
            self.store.audit("system", "enroll.failed"); raise TargetError("wrong enrollment code", 403)
        self.store.x("DELETE FROM enroll")
        token, cid = secrets.token_urlsafe(32), "cred_" + secrets.token_hex(4)
        exp = time.time() + days * 86400 if days else None
        self.store.x("INSERT INTO credentials(id,token_hash,controller,created,expires) VALUES(?,?,?,?,?)", (cid, sha(token), controller[:60], time.time(), exp))
        self.store.audit(controller, "enroll.ok", credential=cid)
        self.say(f"[{time.strftime('%H:%M:%S')}] ENROLLED controller '{controller}' as {cid}")
        return {"token": token, "credential_id": cid, "worker_id": self.id, "name": self.name, "expires": exp}

    def authenticate(self, header: str) -> dict | None:
        scheme, _, tok = header.partition(" ")
        if scheme.lower() != "bearer" or not tok:
            return None
        r = self.store.one("SELECT * FROM credentials WHERE token_hash=? AND revoked=0", (sha(tok),))
        if not r or (r["expires"] and time.time() > r["expires"]):
            return None
        self.store.x("UPDATE credentials SET last_used=? WHERE id=?", (time.time(), r["id"]))
        return r

    def credentials(self) -> list[dict]:
        return self.store.all("SELECT id,controller,created,expires,revoked,last_used FROM credentials ORDER BY created")

    def revoke(self, cid: str, actor: str) -> None:
        self.store.x("UPDATE credentials SET revoked=1 WHERE id=?", (cid,)); self.store.audit(actor, "credential.revoked", credential=cid)

    def revoke_all(self, actor="local-owner") -> None:
        self.store.x("UPDATE credentials SET revoked=1"); self.store.audit(actor, "credential.revoked_all")

    # ---- status / resources
    def status(self) -> dict:
        import platform
        import psutil
        q = {r["status"]: r["n"] for r in self.store.all("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        return {"worker_id": self.id, "name": self.name, "version": VERSION, "os": platform.system().lower(), "host": platform.node(),
                "time": time.time(), "boot": psutil.boot_time(), "cpu": psutil.cpu_percent(interval=0.1), "ram": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage(str(self.dir)).percent, "active_tasks": q.get("running", 0), "queue": q,
                "locked": self.locked(), "authorized_until": self.authorized_until(), "auto_resume": self.auto_resume,
                "secret_store": "os-backed" if getattr(self.prot, "os_backed", False) else "NOT OS-backed (dev)",
                "clients": [{"id": c["id"], "name": c["name"]} for c in self.store.all("SELECT id,name FROM clients")],
                "tools": {k: v["found"] for k, v in detect_tools(["git", "node", "python", "claude", "docker", "tailscale", "rustdesk"]).items()}}

    # ---- clients, secrets, projects
    def ws(self, cid: str) -> Path:
        return self.dir / "workspaces" / cid

    def client(self, cid: str) -> dict:
        c = self.store.one("SELECT * FROM clients WHERE id=?", (cid,))
        if not c:
            raise TargetError("unknown client", 404)
        if c["authorized_until"] and time.time() > c["authorized_until"]:
            raise TargetError("this client's authorization has expired", 403)
        c["systems"] = json.loads(c["systems"]); return c

    def create_client(self, cid: str, name: str, systems: list[dict], days: float | None, actor: str) -> dict:
        if not SLUG.match(cid):
            raise TargetError("client id must be 2-41 chars: a-z 0-9 _ -", 422)
        if self.store.one("SELECT id FROM clients WHERE id=?", (cid,)):
            raise TargetError("client already exists", 409)
        for s in systems:
            if not (isinstance(s, dict) and HOST.match(str(s.get("host", ""))) and isinstance(s.get("port"), int) and 0 < s["port"] < 65536):
                raise TargetError("approved systems must be [{host, port}]", 422)
        self.ws(cid).mkdir(parents=True)
        self.store.x("INSERT INTO clients(id,name,systems,authorized_until,created) VALUES(?,?,?,?,?)",
                     (cid, name[:100], json.dumps(systems), time.time() + days * 86400 if days else None, time.time()))
        self.store.audit(actor, "client.created", client=cid, systems=len(systems))
        return self.client(cid)

    def delete_client(self, cid: str, actor: str) -> None:
        self.client(cid)
        if self.store.one("SELECT id FROM tasks WHERE client_id=? AND status='running'", (cid,)):
            raise TargetError("client has running tasks; cancel them first", 409)
        shutil.rmtree(self.ws(cid), ignore_errors=True); shutil.rmtree(self.dir / "secrets" / cid, ignore_errors=True)
        for t in ("projects", "tasks", "checkpoints"):
            self.store.x(f"DELETE FROM {t} WHERE client_id=?", (cid,))
        shutil.rmtree(self.dir / "checkpoints" / cid, ignore_errors=True)
        self.store.x("DELETE FROM clients WHERE id=?", (cid,)); self.jms.pop(cid, None)
        self.store.audit(actor, "client.deleted", client=cid)

    def set_secret(self, cid: str, name: str, value: str, actor: str) -> None:
        self.client(cid)
        if not SECRET_NAME.match(name) or not value or len(value) > 4096:
            raise TargetError("secret name must be UPPER_SNAKE_CASE; value 1-4096 chars", 422)
        d = self.dir / "secrets" / cid; d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.enc").write_bytes(self.prot.protect(value.encode()))  # raises on platforms without protection
        self.store.audit(actor, "secret.set", client=cid, name=name)  # never the value

    def secret_names(self, cid: str) -> list[str]:
        self.client(cid)
        d = self.dir / "secrets" / cid
        return sorted(p.stem for p in d.glob("*.enc")) if d.exists() else []

    def _secret(self, cid: str, name: str) -> str | None:
        f = self.dir / "secrets" / cid / f"{name}.enc"
        return self.prot.unprotect(f.read_bytes()).decode() if f.exists() else None

    def client_env(self, cid: str, project: dict | None) -> dict:
        """ONLY this client's secrets, and a client-specific Claude config dir. Nothing from other clients or the owner."""
        home = self.ws(cid) / ".claude-home"; home.mkdir(exist_ok=True)
        env = {"CLAUDE_CONFIG_DIR": str(home)}
        k = self._secret(cid, "ANTHROPIC_API_KEY")
        if k:
            env["ANTHROPIC_API_KEY"] = k
        for n in (project or {}).get("env_secrets", []):
            v = self._secret(cid, n)
            if v:
                env[n] = v
        return env

    def jm(self, cid: str) -> JobManager:
        self.client(cid)
        if cid not in self.jms:
            self.jms[cid] = JobManager(FsGuard([self.ws(cid)]))
        return self.jms[cid]

    def guard(self, cid: str) -> FsGuard:
        return self.jm(cid).guard

    def create_project(self, cid: str, name: str, git_url, build_cmd, test_cmd, deploy_cmd, env_secrets, actor: str) -> dict:
        self.client(cid)
        if not SLUG.match(name):
            raise TargetError("project name must be 2-41 chars: a-z 0-9 _ -", 422)
        for c in (build_cmd, test_cmd, deploy_cmd):
            if c is not None and not (isinstance(c, list) and c and all(isinstance(x, str) and x for x in c)):
                raise TargetError("commands must be argument lists", 422)
        if git_url and not GIT_URL.match(git_url):
            raise TargetError("unsupported git URL", 422)
        if not all(SECRET_NAME.match(s) for s in env_secrets):
            raise TargetError("env_secrets must be secret names", 422)
        if self.store.one("SELECT name FROM projects WHERE client_id=? AND name=?", (cid, name)):
            raise TargetError("project already exists", 409)
        (self.ws(cid) / name).mkdir(exist_ok=True)
        self.store.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)", (cid, name, json.dumps(build_cmd), json.dumps(test_cmd), json.dumps(deploy_cmd), json.dumps(env_secrets), git_url, time.time()))
        self.store.audit(actor, "project.created", client=cid, project=name)
        return self.project(cid, name)

    def project(self, cid: str, name: str) -> dict:
        p = self.store.one("SELECT * FROM projects WHERE client_id=? AND name=?", (cid, name))
        if not p:
            raise TargetError("unknown project", 404)
        for k in ("build_cmd", "test_cmd", "deploy_cmd", "env_secrets"):
            p[k] = json.loads(p[k])
        return p

    def projects(self, cid: str) -> list[dict]:
        self.client(cid)
        return [self.project(cid, r["name"]) for r in self.store.all("SELECT name FROM projects WHERE client_id=? ORDER BY name", (cid,))]

    # ---- files / terminal (always inside ONE client's workspace)
    def fs(self, cid: str, op: str, path: str, data: bytes | None = None, actor="controller"):
        g = self.guard(cid)
        if op == "list":
            return g.list(path or str(self.ws(cid)))
        if op == "read":
            return g.read(path)
        if op == "write":
            g.write(path, data or b""); self.store.audit(actor, "fs.write", client=cid, path=path); return None
        if op == "delete":
            g.delete(path); self.store.audit(actor, "fs.delete", client=cid, path=path); return None
        raise TargetError("bad op")

    def exec(self, cid: str, argv: list[str], cwd: str | None, timeout: int, actor: str) -> str:
        self.need_ready()
        if not self.allow_terminal:
            raise TargetError("terminal is disabled on this worker", 403)
        job = self.jm(cid).start(argv, cwd or str(self.ws(cid)), timeout, scrub=True)  # scrubbed env: no secrets in a terminal
        self.store.audit(actor, "terminal.exec", client=cid, argv=redact_argv(argv))
        return job.id

    def job(self, cid: str, jid: str, since: int) -> dict:
        return self.jm(cid).get(jid).snapshot(since)

    def job_cancel(self, cid: str, jid: str) -> dict:
        return self.jm(cid).cancel(jid).snapshot()

    def check_system(self, cid: str, host: str, port: int) -> dict:
        """TCP reachability of ONE approved system. Anything not on the client's approved list is refused: no scanning."""
        c = self.client(cid)
        if not any(s["host"].lower() == host.lower() and s["port"] == port for s in c["systems"]):
            self.store.audit("controller", "network.refused", client=cid, host=host, port=port)
            raise TargetError("that system/port is not on this client's approved list", 403)
        import socket
        try:
            socket.create_connection((host, port), timeout=3).close(); ok = True
        except OSError:
            ok = False
        return {"host": host, "port": port, "reachable": ok}

    # ---- tasks
    def _ev(self, tid: str, msg: str, level="info") -> None:
        self.store.x("INSERT INTO events(task_id,ts,level,msg) VALUES(?,?,?,?)", (tid, time.time(), level, clean(msg)[:600]))

    def _save(self, t: dict) -> None:
        self.store.x("UPDATE tasks SET status=?, updated=?, data=? WHERE id=?", (t["status"], time.time(), json.dumps(t["data"]), t["id"]))

    def task(self, tid: str, since: int = 0) -> dict:
        r = self.store.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not r:
            raise TargetError("task not found", 404)
        r["data"] = json.loads(r["data"])
        ev = self.store.all("SELECT seq,ts,level,msg FROM events WHERE task_id=? AND seq>? ORDER BY seq", (tid, since))
        r["events"] = ev; r["last_seq"] = ev[-1]["seq"] if ev else since
        return r

    def tasks(self, cid: str | None = None, status: str | None = None) -> list[dict]:
        q, a = "SELECT id FROM tasks WHERE 1=1", []
        if cid:
            q += " AND client_id=?"; a.append(cid)
        if status:
            q += " AND status=?"; a.append(status)
        out = []
        for r in self.store.all(q + " ORDER BY created DESC LIMIT 200", tuple(a)):
            t = self.task(r["id"]); t.pop("events"); out.append(t)
        return out

    def create_task(self, cid: str, project: str, kind: str, instruction: str, argv: list[str] | None, production: bool, allow_bash: bool, actor: str) -> dict:
        self.need_ready()
        self.client(cid)
        p = self.project(cid, project)
        if kind not in KINDS:
            raise TargetError(f"kind must be one of {sorted(KINDS)}", 422)
        if kind == "claude" and not instruction.strip():
            raise TargetError("instruction required", 422)
        if kind == "command":
            if not argv:
                raise TargetError("argv required", 422)
        if kind == "deploy":
            if not p["deploy_cmd"]:
                raise TargetError("this project has no deploy command", 409)
            argv, production = p["deploy_cmd"], True  # deployments are always production
        if kind in ("git_clone", "git_pull") and not p["git_url"]:
            raise TargetError("this project has no git URL", 409)
        tid = "tsk_" + secrets.token_hex(5)
        data = {"instruction": instruction[:4000], "argv": argv, "production": production, "allow_bash": allow_bash, "destructive": kind in DESTRUCTIVE,
                "created_by": actor, "approved_by": None, "approved_hash": None, "session_id": None, "resume": False, "attempt": None, "result": None}
        self.store.x("INSERT INTO tasks(id,client_id,project,kind,status,created,updated,data) VALUES(?,?,?,?,?,?,?,?)",
                     (tid, cid, project, kind, "awaiting_approval", time.time(), time.time(), json.dumps(data)))
        self._ev(tid, f"task created by {actor}: {kind} on {cid}/{project}" + (" [PRODUCTION]" if production else ""))
        self.store.audit(actor, "task.created", task=tid, client=cid, project=project, kind=kind, production=production)
        return self.task(tid)

    def _hash(self, t: dict) -> str:
        d = t["data"]
        return sha(json.dumps([t["client_id"], t["project"], t["kind"], d["instruction"], d["argv"], d["production"], d["allow_bash"]], sort_keys=True))

    def approve(self, tid: str, approver: str, confirm_text: str | None) -> dict:
        self.need_ready()
        t = self.task(tid)
        if t["status"] != "awaiting_approval":
            raise TargetError(f"task is {t['status']}", 409)
        if t["data"]["production"]:
            need = f"{self.client(t['client_id'])['name']}/{t['project']}"
            if (confirm_text or "").strip() != need:
                raise TargetError(f"production change: type exactly '{need}' to confirm", 428)
        t["data"].update(approved_by=approver, approved_hash=self._hash(t)); t["status"] = "queued"
        self._save(t); self._ev(tid, f"approved by {approver}; queued")
        self.store.audit(approver, "task.approved", task=tid, production=t["data"]["production"])
        return self.task(tid)

    def cancel(self, tid: str, actor: str) -> dict:
        t = self.task(tid)
        if t["status"] in FINAL:
            return t
        self.cancel_flags.add(tid)
        if t["status"] != "running":
            t["status"] = "cancelled"; self._save(t); self.cancel_flags.discard(tid)
        self._ev(tid, f"cancel requested by {actor}", "warn"); self.store.audit(actor, "task.cancel", task=tid)
        return self.task(tid)

    def pause(self, tid: str, actor: str) -> dict:
        t = self.task(tid)
        if t["status"] == "queued":
            t["status"] = "paused"; self._save(t)
        elif t["status"] == "running" and t["kind"] == "claude" and t["data"]["session_id"]:
            t["data"]["pause_requested"] = True; self._save(t); self.cancel_flags.add(tid)
        else:
            raise TargetError("only queued tasks or running Claude tasks (with a session) can be paused", 409)
        self._ev(tid, f"pause requested by {actor}"); return self.task(tid)

    def resume(self, tid: str, actor: str) -> dict:
        """Safe resume: paused tasks and interrupted *non-destructive* Claude tasks with a captured session. Destructive work is never resumed."""
        self.need_ready()
        t = self.task(tid)
        if t["data"]["destructive"] or t["status"] == "needs_review":
            raise TargetError("destructive tasks are never resumed automatically; use retry (needs a fresh approval)", 409)
        if t["status"] == "paused":
            t["data"]["pause_requested"] = False
        elif t["status"] != "interrupted":
            raise TargetError(f"task is {t['status']}", 409)
        if t["kind"] == "claude" and t["data"]["session_id"]:
            t["data"]["resume"] = True
        t["status"] = "queued"; self._save(t); self._ev(tid, f"resumed by {actor}"); self.store.audit(actor, "task.resumed", task=tid)
        return self.task(tid)

    def retry(self, tid: str, actor: str) -> dict:
        t = self.task(tid)
        if t["status"] not in ("failed", "interrupted", "needs_review", "cancelled", "paused"):
            raise TargetError(f"task is {t['status']}", 409)
        t["data"].update(approved_by=None, approved_hash=None, resume=False, attempt=None, pause_requested=False, result=None); t["status"] = "awaiting_approval"
        self._save(t); self._ev(tid, f"retry requested by {actor}: needs a NEW approval"); self.store.audit(actor, "task.retry", task=tid)
        return self.task(tid)

    # ---- checkpoints
    def checkpoint(self, t: dict) -> dict:
        src = self.ws(t["client_id"]) / t["project"]
        cid = "cp_" + secrets.token_hex(4)
        d = self.dir / "checkpoints" / t["client_id"]; d.mkdir(parents=True, exist_ok=True)
        out = d / f"{cid}.tar.gz"
        total = 0
        with tarfile.open(out, "w:gz") as tf:
            for p in sorted(src.rglob("*")):
                rel = p.relative_to(src)
                if set(rel.parts) & SKIP_DIRS or p.is_symlink() or not p.is_file():
                    continue
                total += p.stat().st_size
                if total > MAX_CHECKPOINT:
                    out.unlink(missing_ok=True); raise TargetError("project is larger than 100 MB; refusing to run without a checkpoint", 413)
                tf.add(p, arcname=str(rel))
        self.store.x("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)", (cid, t["id"], t["client_id"], t["project"], str(out), time.time(), out.stat().st_size))
        self._ev(t["id"], f"checkpoint {cid} saved ({out.stat().st_size // 1024} KB)")
        return {"id": cid}

    def checkpoints(self, tid: str) -> list[dict]:
        return self.store.all("SELECT id,task_id,project,created,size FROM checkpoints WHERE task_id=? ORDER BY created", (tid,))

    def restore(self, cpid: str, actor: str) -> dict:
        cp = self.store.one("SELECT * FROM checkpoints WHERE id=?", (cpid,))
        if not cp:
            raise TargetError("checkpoint not found", 404)
        self.client(cp["client_id"])
        dst = (self.ws(cp["client_id"]) / cp["project"]).resolve()
        n = 0
        with tarfile.open(cp["path"]) as tf:
            for m in tf.getmembers():
                target = (dst / m.name).resolve()
                if dst not in target.parents or not m.isfile():
                    continue  # never write outside the project
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read()); n += 1  # type: ignore[union-attr]
        self.store.audit(actor, "checkpoint.restored", checkpoint=cpid, files=n)
        return {"restored": n}

    # ---- execution
    def recover(self) -> None:
        """After a crash/restart: running tasks become `interrupted`; destructive ones that had started become `needs_review`.
        Nothing is re-run here unless auto_resume is on AND the task is a non-destructive Claude task with a saved session."""
        for r in self.store.all("SELECT id FROM tasks WHERE status='running'"):
            t = self.task(r["id"])
            if t["data"]["destructive"] and t["data"]["attempt"]:
                t["status"] = "needs_review"; msg = "worker restarted while a DESTRUCTIVE step was in progress: NOT re-run. Inspect the target, then `retry` with a new approval."
            else:
                t["status"] = "interrupted"; msg = "worker restarted: task interrupted."
            msg += self._reap(t); t["data"]["proc"] = None
            if self.auto_resume and t["kind"] == "claude" and t["data"]["session_id"] and not t["data"]["destructive"]:
                t["status"] = "queued"; t["data"]["resume"] = True; msg += " auto_resume: re-queued with the saved Claude session."
            self._save(t); self._ev(t["id"], msg, "warn"); self.store.audit("system", "task.recovered", task=t["id"], status=t["status"])

    @staticmethod
    def _reap(t: dict) -> str:
        """Kill a job process left over from before a crash (only if pid AND start time still match: no PID-reuse accidents)."""
        pr = t["data"].get("proc")
        if not pr:
            return ""
        try:
            import psutil
            p = psutil.Process(pr["pid"])
            if abs(p.create_time() - pr["ctime"]) > 1:
                return ""
            for ch in p.children(recursive=True):
                ch.kill()
            p.kill()
            return " Orphaned job process stopped."
        except Exception:  # noqa: BLE001
            return ""

    def _track(self, tid: str, job) -> None:
        try:
            import psutil
            t = self.task(tid)
            t["data"]["proc"] = {"pid": job.proc.pid, "ctime": psutil.Process(job.proc.pid).create_time()}
            self._save(t)
        except Exception:  # noqa: BLE001
            pass

    def start(self) -> None:
        if not self.sched.is_alive():  # serve() may be called again after a network-level restart of the listener
            self.sched.start()

    def _scheduler(self) -> None:
        stopfile = self.dir / "STOP"
        while not self.stop.wait(self.tick):
            try:
                if stopfile.exists() and not self.locked():
                    self.lock("STOP file present (local emergency stop)", "local-owner")
                until = self.authorized_until()
                if until and time.time() > until and not self.locked():
                    self.lock("authorization window expired")
                if self.locked():
                    continue
                busy = {(t.get("client_id"), t.get("project")) for t in [self.task(r["id"]) for r in self.store.all("SELECT id FROM tasks WHERE status='running'")]}
                for r in self.store.all("SELECT id FROM tasks WHERE status='queued' ORDER BY created"):
                    t = self.task(r["id"])
                    key = (t["client_id"], t["project"])
                    if key in busy or not self.slots.acquire(blocking=False):
                        continue
                    if t["data"]["approved_hash"] != self._hash(t):
                        t["status"] = "failed"; self._save(t); self._ev(t["id"], "task changed after approval: refused", "error"); self.slots.release(); continue
                    t["status"] = "running"; self._save(t); busy.add(key)
                    th = threading.Thread(target=self._run, args=(t["id"],), daemon=True); self.running[t["id"]] = th; th.start()
            except Exception as e:  # noqa: BLE001
                self.say(f"scheduler error: {type(e).__name__}: {e}")

    def _run(self, tid: str) -> None:
        t = self.task(tid)
        try:
            self._ev(tid, "▶ started")
            res = getattr(self, f"_do_{t['kind']}")(t)
            t = self.task(tid)
            if tid in self.cancel_flags:
                paused = t["data"].get("pause_requested")
                t["status"] = "paused" if paused else "cancelled"
                self._ev(tid, "paused (resumable)" if paused else "cancelled", "warn")
            else:
                t["data"]["result"] = res; t["status"] = res["status"]
                self._ev(tid, f"{'✔' if res['status'].startswith('completed') else '✖'} {res['detail']}", "info" if res["status"].startswith("completed") else "error")
        except (TargetError, OSError, ValueError) as e:
            t = self.task(tid); t["status"] = "failed"; t["data"]["result"] = {"status": "failed", "detail": str(e)}; self._ev(tid, f"✖ {e}", "error")
        finally:
            self._save(t); self.cancel_flags.discard(tid); self.running.pop(tid, None); self.slots.release()
            self.store.audit("system", "task.finished", task=tid, status=t["status"])

    def _stream(self, t: dict, jm: JobManager, job) -> dict:
        tid, buf, since, lines = t["id"], "", 0, 0
        while True:
            if tid in self.cancel_flags or self.locked():
                jm.cancel(job.id)
            s = job.snapshot(since); since = s["next"]; buf += s["output"]
            *done, buf = buf.split("\n")
            for ln in done:
                self._line(t, ln)
            if s["done"]:
                if buf.strip():
                    self._line(t, buf)
                return s
            time.sleep(0.1)

    def _line(self, t: dict, ln: str) -> None:
        try:
            j = json.loads(ln)
            if j.get("type") == "system" and j.get("session_id") and not t["data"]["session_id"]:
                t["data"]["session_id"] = j["session_id"]; self._save(t); self._ev(t["id"], f"claude session {j['session_id']} (resumable)")
        except json.JSONDecodeError:
            pass
        ev = claude_event(ln)
        if ev:
            self._ev(t["id"], ev)

    def _verify(self, t: dict, p: dict, jm: JobManager) -> dict:
        if not p["test_cmd"]:
            return {"status": "completed_unverified", "detail": "finished; no test command configured, so the result is NOT verified"}
        j = jm.start(p["test_cmd"], str(self.ws(t["client_id"]) / t["project"]), 1800, scrub=True, extra_env=self.client_env(t["client_id"], p))
        s = self._stream_plain(t, j)
        ok = s["exit"] == 0
        return {"status": "completed" if ok else "failed", "detail": f"tests {' '.join(p['test_cmd'])} -> exit {s['exit']}"}

    def _stream_plain(self, t: dict, job) -> dict:
        since = 0
        while True:
            if t["id"] in self.cancel_flags or self.locked():
                job.proc.kill() if job.proc else None
            s = job.snapshot(since)
            for ln in s["output"].splitlines():
                self._ev(t["id"], ln)
            since = s["next"]
            if s["done"]:
                return s
            time.sleep(0.1)

    def _do_claude(self, t: dict) -> dict:
        cid, d = t["client_id"], t["data"]
        p = self.project(cid, t["project"]); jm = self.jm(cid)
        if not d["resume"]:
            self.checkpoint(t)
        argv = claude_argv("Continue the task." if d["resume"] else d["instruction"] + "\n\nRules: work only inside the current directory; never read or print secrets; "
                           "do not delete files unless asked; explain what you changed.", "acceptEdits", CLAUDE_TOOLS + (["Bash"] if d["allow_bash"] else []),
                           d["session_id"] if d["resume"] else None)
        job = jm.start(argv, str(self.ws(cid) / t["project"]), 3600, scrub=True, extra_env=self.client_env(cid, p))
        self._track(t["id"], job)
        fresh = self.task(t["id"]); fresh["data"]["resume"] = False; self._save(fresh)
        s = self._stream(t, jm, job)
        if t["id"] in self.cancel_flags:
            return {"status": "cancelled", "detail": "cancelled"}
        if s["exit"] != 0:
            return {"status": "failed", "detail": f"Claude Code exited {s['exit']}"}
        return self._verify(t, p, jm)

    def _simple(self, t: dict, argv: list[str], cwd: Path, destructive: bool, verify: bool) -> dict:
        cid, p = t["client_id"], self.project(t["client_id"], t["project"])
        jm = self.jm(cid)
        if destructive:
            self.checkpoint(t)
            fresh = self.task(t["id"]); fresh["data"]["attempt"] = time.time(); self._save(fresh)  # persisted BEFORE the action starts
        job = jm.start(argv, str(cwd), 3600, scrub=True, extra_env=self.client_env(cid, p))
        self._track(t["id"], job)
        s = self._stream_plain(t, job)
        if destructive:
            fresh = self.task(t["id"]); fresh["data"]["attempt"] = None; self._save(fresh)
        if t["id"] in self.cancel_flags:
            return {"status": "cancelled", "detail": "cancelled"}
        if s["exit"] != 0:
            return {"status": "failed", "detail": f"{argv[0]} exited {s['exit']}"}
        return self._verify(t, p, jm) if verify else {"status": "completed", "detail": f"{argv[0]} exited 0"}

    def _do_command(self, t):
        return self._simple(t, t["data"]["argv"], self.ws(t["client_id"]) / t["project"], True, False)

    def _do_deploy(self, t):
        return self._simple(t, t["data"]["argv"], self.ws(t["client_id"]) / t["project"], True, True)

    def _do_git_pull(self, t):
        return self._simple(t, ["git", "pull", "--ff-only"], self.ws(t["client_id"]) / t["project"], False, False)

    def _do_git_clone(self, t):
        p = self.project(t["client_id"], t["project"])
        return self._simple(t, ["git", "clone", "--", p["git_url"], "."], self.ws(t["client_id"]) / t["project"], False, False)

    # ---- emergency / removal
    def emergency_stop(self, actor: str) -> dict:
        self.lock(f"emergency stop by {actor}", actor)
        return {"locked": True}

    def firewall_script(self, infra: list[str]) -> str:
        """PowerShell that blocks outbound traffic to the client's private LAN except approved systems + infrastructure (gateway/DNS) + Tailscale.
        Windows Firewall BLOCK rules beat ALLOW rules, so the block list is computed as (private ranges − exceptions)."""
        keep = set()
        for c in self.store.all("SELECT id,systems FROM clients"):
            for s in json.loads(c["systems"]):
                try:
                    keep.add(ipaddress.ip_network(s["host"] + "/32"))
                except ValueError:
                    pass  # hostnames: resolve them to addresses and pass via --infra if they are on the LAN
        for i in infra:
            keep.add(ipaddress.ip_network(i + "/32"))
        blocked = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
        for k in keep:
            nxt = []
            for b in blocked:
                nxt += list(b.address_exclude(k)) if k.version == b.version and k.subnet_of(b) else [b]
            blocked = nxt
        blocked = list(ipaddress.collapse_addresses(blocked))
        return ("# Claude IT Technician worker — outbound LAN isolation. Review, then run in an elevated PowerShell. Remove with the last line.\n"
                "# Allowed to reach on the client's LAN: " + (", ".join(str(k.network_address) for k in sorted(keep, key=str)) or "(nothing)") + "\n"
                "# Not touched: Internet, Tailscale (100.64.0.0/10), loopback.\n"
                f"New-NetFirewallRule -DisplayName 'CIT Worker: block client LAN' -Direction Outbound -Action Block -RemoteAddress {','.join(str(b) for b in blocked)}\n"
                "# Undo: Remove-NetFirewallRule -DisplayName 'CIT Worker: block client LAN'\n")

    def wipe(self, confirm_name: str) -> dict:
        if confirm_name != self.name:
            raise TargetError(f"type the worker name '{self.name}' to confirm", 428)
        self.stop.set(); self.cancel_everything()
        n = 0
        for f in list((self.dir / "secrets").rglob("*")) + [self.dir / "key.enc", self.dir / "dev.key"]:
            if f.is_file():
                try:
                    f.write_bytes(os.urandom(max(64, f.stat().st_size))); n += 1  # best-effort overwrite before delete (SSD wear-levelling makes this non-guaranteed)
                except OSError:
                    pass
        self.store.c.close()
        shutil.rmtree(self.dir, ignore_errors=True)
        return {"overwritten": n, "removed": str(self.dir), "remaining_steps": ["Run `service remove` if you installed the scheduled task", "Remove the Tailscale node and RustDesk if you installed them", "Remove any firewall rule 'CIT Worker: block client LAN'"]}


# ------------------------------------------------------------------ service install (explicit, named, removable)
TASK_NAME = "Claude IT Technician Worker"


def service_commands(exe: str, data_dir: str, user: str, bind: str = "127.0.0.1", port: int = 8777) -> dict:
    # At log on of that user, interactive only (/IT): no stored password, runs in the user's session so DPAPI works.
    # --data-dir is a global option and must precede `run`.
    return {"install": ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{exe}" --data-dir "{data_dir}" run --bind {bind} --port {port}',
                        "/SC", "ONLOGON", "/IT", "/RU", user, "/RL", "LIMITED", "/F"],
            "remove": ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], "show": ["schtasks", "/Query", "/TN", TASK_NAME]}


# ------------------------------------------------------------------ HTTP
FIELDS = {
    "enroll": {"code", "controller", "days"}, "clients": {"id", "name", "systems", "days"}, "secret": {"value"},
    "projects": {"name", "git_url", "build_cmd", "test_cmd", "deploy_cmd", "env_secrets"}, "exec": {"argv", "cwd", "timeout"},
    "fswrite": {"path", "content_b64"}, "fsdel": {"path"}, "tasks": {"client_id", "project", "kind", "instruction", "argv", "production", "allow_bash"},
    "approve": {"approver", "confirm_text"}, "who": {"actor"}, "unlock": {"confirm"}, "system": {"host", "port"}, "restore": set(),
}


def make_handler(w: Worker):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"; server_version = "cit-worker"

        def log_message(self, *a): pass

        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def _body(self, name):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 8 * 1024 * 1024:
                raise TargetError("body too large", 413)
            d = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(d, dict) or set(d) - FIELDS[name]:
                raise TargetError(f"unexpected fields: {sorted(set(d) - FIELDS[name]) if isinstance(d, dict) else 'body'}")
            return d

        def _handle(self, method):
            try:
                if not _private_ip(ipaddress.ip_address(self.client_address[0].split("%")[0])):
                    return self._send(403, {"detail": "connections from public addresses are refused"})
                u = urlparse(self.path); path = u.path.rstrip("/") or "/"; q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if method == "POST" and path == "/enroll":
                    d = self._body("enroll")
                    return self._send(200, w.enroll(str(d.get("code", "")), str(d.get("controller", "controller")), d.get("days")))
                cred = w.authenticate(self.headers.get("Authorization", ""))
                if not cred:
                    return self._send(401, {"detail": "not enrolled, credential revoked or expired"})
                self._route(method, path, q, cred["controller"], cred["id"])
            except TargetError as e:
                self._send(e.status, {"detail": str(e)})
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                self._send(400, {"detail": f"bad request: {e}"})
            except RuntimeError as e:
                self._send(501, {"detail": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"detail": f"worker error: {type(e).__name__}"})

        def _route(self, m, path, q, who, credid):
            P = path.strip("/").split("/")
            if (m, path) == ("GET", "/status"):
                return self._send(200, w.status())
            if (m, path) == ("POST", "/unlock"):
                d = self._body("unlock")
                if not d.get("confirm"):
                    raise TargetError("confirm required", 428)
                w.unlock(who); return self._send(200, w.status())
            if (m, path) == ("POST", "/emergency-stop"):
                self._body("who"); return self._send(200, w.emergency_stop(who))
            if (m, path) == ("GET", "/credentials"):
                return self._send(200, w.credentials())
            if m == "DELETE" and P[0] == "credentials" and len(P) == 2:
                w.revoke(P[1], who); return self._send(200, {"ok": True})
            if (m, path) == ("GET", "/audit"):
                return self._send(200, w.store.all("SELECT ts,actor,action,detail FROM audit ORDER BY seq DESC LIMIT 200"))
            if (m, path) == ("GET", "/firewall-script"):
                return self._send(200, {"script": w.firewall_script([i for i in q.get("infra", "").split(",") if i])})
            if not (m == "GET" and P[0] == "tasks"):  # task history/logs stay readable while locked so you can see what happened
                w.need_ready()
            if (m, path) == ("GET", "/clients"):
                return self._send(200, [{**c, "systems": json.loads(c["systems"])} for c in w.store.all("SELECT * FROM clients")])
            if (m, path) == ("POST", "/clients"):
                d = self._body("clients")
                return self._send(201, w.create_client(d["id"], d["name"], d.get("systems", []), d.get("days"), who))
            if P[0] == "clients" and len(P) >= 2:
                cid = P[1]
                if m == "DELETE" and len(P) == 2:
                    w.delete_client(cid, who); return self._send(200, {"ok": True})
                if (m, len(P), P[2:3]) == ("GET", 3, ["secrets"]):
                    return self._send(200, w.secret_names(cid))
                if m == "PUT" and P[2:3] == ["secrets"] and len(P) == 4:
                    w.set_secret(cid, P[3], str(self._body("secret")["value"]), who); return self._send(200, {"ok": True})
                if (m, P[2:3]) == ("GET", ["projects"]):
                    return self._send(200, w.projects(cid))
                if (m, P[2:3]) == ("POST", ["projects"]):
                    d = self._body("projects")
                    return self._send(201, w.create_project(cid, d["name"], d.get("git_url"), d.get("build_cmd"), d.get("test_cmd"), d.get("deploy_cmd"), d.get("env_secrets", []), who))
                if (m, P[2:3]) == ("GET", ["fs"]) and len(P) == 4 and P[3] == "list":
                    return self._send(200, {"entries": w.fs(cid, "list", q.get("path", ""))})
                if (m, P[2:3]) == ("GET", ["fs"]) and len(P) == 4 and P[3] == "read":
                    return self._send(200, {"content_b64": b64e(w.fs(cid, "read", q.get("path", "")))})
                if (m, P[2:3]) == ("PUT", ["fs"]) and len(P) == 4 and P[3] == "write":
                    d = self._body("fswrite"); w.fs(cid, "write", d["path"], b64d(d["content_b64"]), who); return self._send(200, {"ok": True})
                if (m, P[2:3]) == ("POST", ["fs"]) and len(P) == 4 and P[3] == "delete":
                    w.fs(cid, "delete", self._body("fsdel")["path"], None, who); return self._send(200, {"ok": True})
                if (m, P[2:3]) == ("POST", ["exec"]):
                    d = self._body("exec"); return self._send(200, {"job": w.exec(cid, d["argv"], d.get("cwd"), int(d.get("timeout", 300)), who)})
                if (m, P[2:3]) == ("POST", ["system-check"]):
                    d = self._body("system"); return self._send(200, w.check_system(cid, str(d["host"]), int(d["port"])))
                if P[2:3] == ["jobs"] and len(P) >= 4:
                    if m == "GET":
                        return self._send(200, w.job(cid, P[3], int(q.get("since", 0))))
                    if m == "POST" and P[4:5] == ["cancel"]:
                        return self._send(200, w.job_cancel(cid, P[3]))
            if (m, path) == ("GET", "/tasks"):
                return self._send(200, w.tasks(q.get("client_id"), q.get("status")))
            if (m, path) == ("POST", "/tasks"):
                d = self._body("tasks")
                return self._send(201, w.create_task(d["client_id"], d["project"], d["kind"], str(d.get("instruction", "")), d.get("argv"), bool(d.get("production")), bool(d.get("allow_bash")), who))
            if P[0] == "tasks" and len(P) >= 2:
                tid = P[1]
                if m == "GET" and len(P) == 2:
                    return self._send(200, w.task(tid, int(q.get("since", 0))))
                if m == "POST" and len(P) == 3:
                    act = P[2]
                    if act == "approve":
                        d = self._body("approve"); return self._send(200, w.approve(tid, d.get("approver") or who, d.get("confirm_text")))
                    if act in ("cancel", "pause", "resume", "retry"):
                        self._body("who"); return self._send(200, getattr(w, act)(tid, who))
                if (m, P[2:3]) == ("GET", ["checkpoints"]):
                    return self._send(200, w.checkpoints(tid))
            if m == "POST" and P[0] == "checkpoints" and len(P) == 3 and P[2] == "restore":
                self._body("restore"); return self._send(200, w.restore(P[1], who))
            self._send(404, {"detail": "unknown route"})

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PUT(self): self._handle("PUT")
        def do_DELETE(self): self._handle("DELETE")

    return H


def serve(w: Worker, bind: str, port: int):
    ctx, fp = w.ssl_context()

    class Quiet(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"  # on Windows SO_REUSEADDR lets a second process bind the same port and receive connections

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def handle_error(self, request, client_address): pass

    srv = Quiet((bind, port), make_handler(w)); srv.socket = ctx.wrap_socket(srv.socket, server_side=True); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    w.start()
    return srv, fp


# ------------------------------------------------------------------ CLI
def single_instance(data_dir: Path):
    """Non-blocking exclusive lock on <data-dir>/instance.lock; None if another worker already owns this data directory."""
    f = open(data_dir / "instance.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close(); return None
    return f


def default_dir() -> Path:
    base = os.environ.get("PROGRAMDATA") if os.name == "nt" else str(Path.home())
    return Path(base or ".") / ("ClaudeITWorker" if os.name == "nt" else ".cit-worker")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a frozen exe buffers when not on a console (boot task, pipe): the banner/log would never appear
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=default_dir()); p.add_argument("--dev-insecure-store", action="store_true", help="non-Windows testing only: file-key secret store (NOT OS-backed)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the worker service (visible console)"); r.add_argument("--bind", default="127.0.0.1", help="use the Tailscale IP (100.x) for remote control"); r.add_argument("--port", type=int, default=8777)
    r.add_argument("--auto-resume", action="store_true", help="re-queue interrupted NON-destructive Claude tasks that have a saved session"); r.add_argument("--max-parallel", type=int, default=1)
    sub.add_parser("enroll", help="print a one-time code (10 min) so your laptop can pair with this worker")
    a_ = sub.add_parser("authorize", help="set/extend the client authorization window; the worker locks itself when it ends"); a_.add_argument("--days", type=float, required=True)
    sub.add_parser("lock", help="immediately lock: stop all jobs, refuse everything"); sub.add_parser("unlock"); sub.add_parser("revoke-all", help="revoke every controller credential")
    sub.add_parser("status")
    f = sub.add_parser("firewall-script", help="print a PowerShell script isolating the client's LAN"); f.add_argument("--infra", default="", help="comma-separated gateway/DNS IPs to keep reachable")
    s = sub.add_parser("service", help="install/remove the visible scheduled task"); s.add_argument("action", choices=["install", "remove", "show"]); s.add_argument("--user", default=""); s.add_argument("--bind", default="127.0.0.1", help="address the boot task listens on (this PC's private LAN/Tailscale address)"); s.add_argument("--port", type=int, default=8777); s.add_argument("--i-understand", action="store_true", help="required for install: the worker will start at boot, visibly, as the named task")
    wp = sub.add_parser("wipe", help="securely remove all worker data"); wp.add_argument("--name", required=True)
    sub.add_parser("selftest")
    a = p.parse_args(argv)
    a.data_dir.mkdir(parents=True, exist_ok=True)
    prot = make_protector(a.data_dir, a.dev_insecure_store)
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "service":
        cmds = service_commands(sys.executable if not getattr(sys, "frozen", False) else sys.executable, str(a.data_dir), a.user, a.bind, a.port)
        if a.action == "install" and not (a.i_understand and a.user):
            print("Refusing: pass --user <windows account> and --i-understand.\nThis creates the visible scheduled task:", " ".join(cmds["install"])); return 2
        print(" ".join(cmds[a.action]))
        if os.name == "nt":
            return subprocess.run(cmds[a.action]).returncode
        print("(not Windows: command printed only)"); return 0
    if a.cmd == "run":
        instance_lock = single_instance(a.data_dir)  # held until the process exits
        if instance_lock is None:
            print(f"Another worker is already running with data directory {a.data_dir}. Not starting a second one."); return 3
    w = Worker(a.data_dir, prot, auto_resume=getattr(a, "auto_resume", False), max_parallel=getattr(a, "max_parallel", 1))
    if a.cmd == "enroll":
        print(f"Enrollment code (valid 10 minutes, single use): {w.new_enroll_code()}"); return 0
    if a.cmd == "authorize":
        print("authorized until", time.strftime("%Y-%m-%d %H:%M", time.localtime(w.authorize(a.days)))); return 0
    if a.cmd == "lock":
        w.lock("locked on this machine", "local-owner"); return 0
    if a.cmd == "unlock":
        w.unlock("local-owner"); print("unlocked"); return 0
    if a.cmd == "revoke-all":
        w.revoke_all(); print("all controller credentials revoked"); return 0
    if a.cmd == "status":
        print(json.dumps(w.status(), indent=2)); return 0
    if a.cmd == "firewall-script":
        print(w.firewall_script([i for i in a.infra.split(",") if i])); return 0
    if a.cmd == "wipe":
        print(json.dumps(w.wipe(a.name), indent=2)); return 0
    srv, fp = serve(w, a.bind, a.port)
    spaced = " ".join(fp[i:i + 8] for i in range(0, len(fp), 8)).upper()
    print(f"\n=== Claude IT Technician WORKER {VERSION} ===\nWorker:       {w.name} ({w.id})\nListening:    {a.bind}:{a.port}  (private/Tailscale sources only)\nFingerprint:  {spaced}\n"
          f"Secret store: {'OS-backed' if getattr(prot, 'os_backed', False) else 'NOT OS-backed (dev)'}\nAuthorized:   "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(w.authorized_until())) if w.authorized_until() else 'no expiry set (use `authorize --days N`)'}\n"
          f"Locked:       {w.locked() or 'no'}\nLocal controls: create a file named STOP in {a.data_dir} to lock immediately; `lock`, `revoke-all`, `wipe`.\n")
    try:
        while not w.stop.wait(1):
            pass
    except KeyboardInterrupt:
        w.say("stopping (running tasks become 'interrupted')")
    srv.shutdown(); return 0


def selftest() -> int:
    from app.transports.agent import AgentClient
    d = Path(tempfile.mkdtemp(prefix="cit-wst-"))
    try:
        prot = FileKeyProtector(d)
        w = Worker(d, prot, say=lambda *_: None, tick=0.1)
        srv, fp = serve(w, "127.0.0.1", 0)
        c = AgentClient("127.0.0.1", srv.server_address[1], fp)
        try:
            c._req("POST", "/enroll", {"code": "00000000", "controller": "x"}, auth=False); return fail("enrolled without a code")
        except TargetError:
            pass
        code = w.new_enroll_code()
        tok = c._req("POST", "/enroll", {"code": code, "controller": "selftest"}, auth=False)["token"]; c.token = tok
        assert c._req("GET", "/status")["worker_id"] == w.id
        c._req("POST", "/clients", {"id": "acme", "name": "Acme"}); c._req("POST", "/emergency-stop", {})
        try:
            c._req("GET", "/clients"); return fail("locked worker answered")
        except TargetError as e:
            assert e.status == 423
        srv.shutdown(); w.stop.set(); print("selftest OK: persistent identity, enrollment, authenticated API, client creation, emergency lock"); return 0
    except Exception as e:  # noqa: BLE001
        return fail(repr(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def fail(m):
    print("selftest FAILED:", m); return 1


if __name__ == "__main__":
    sys.exit(main())
