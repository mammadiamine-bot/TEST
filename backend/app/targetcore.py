"""Shared engine behind every execution target (portable agent, this computer).

Safety properties enforced here, in one place:
- filesystem access is confined to explicit project roots (realpath check, symlink-safe)
- commands are argv lists (never a shell string) with timeouts, scrubbed env, capped output
- capabilities are explicit grants chosen by the machine's owner
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE = 5 * 1024 * 1024
MAX_OUTPUT = 5 * 1024 * 1024
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
PERMISSION_MODES = {"default", "acceptEdits", "plan"}  # never bypassPermissions


class TargetError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


@dataclass
class Grants:
    """What the machine owner allowed. Everything defaults to read-only diagnostics."""
    terminal: bool = False   # run argv commands
    write: bool = False      # create/modify/delete files inside project roots
    repair: bool = False     # run repair (state-changing) PowerShell tools
    claude: bool = False     # run Claude Code on this machine (with the machine's own credentials)

    def names(self) -> list[str]:
        return [k for k, v in self.__dict__.items() if v]


# ------------------------------------------------------------------ secrets hygiene
_SECRET_ARG = re.compile(r"(?i)^(--?(pass(word)?|pwd|token|secret|api[-_]?key|auth)[=:]?)(.*)$")
_SECRET_VAL = re.compile(r"(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,})")
_KV = re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*=\s*\S+")


def redact_argv(argv: list[str]) -> list[str]:
    """For audit logs: hide values that look like passwords, tokens or keys."""
    out, hide_next = [], False
    for a in argv:
        if hide_next:
            out.append("<redacted>"); hide_next = False; continue
        m = _SECRET_ARG.match(a)
        if m:
            if m.group(4):
                out.append(m.group(1) + "<redacted>")
            else:
                out.append(a); hide_next = True
            continue
        a = _KV.sub(lambda mm: mm.group(1) + "=<redacted>", a)
        out.append(_SECRET_VAL.sub("<redacted>", a))
    return out


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    return {k: v for k, v in (env or os.environ).items() if not SECRET_ENV.search(k)}


# ------------------------------------------------------------------ filesystem guard
class FsGuard:
    def __init__(self, roots: list[Path | str]):
        self.roots = [Path(os.path.realpath(r)) for r in roots]
        if not self.roots:
            raise TargetError("no project roots authorized")

    def _inside(self, p: Path) -> bool:
        return any(p == r or r in p.parents for r in self.roots)

    def resolve(self, path: str, must_exist: bool = True) -> Path:
        if not path or "\x00" in path:
            raise TargetError("invalid path")
        p = Path(path)
        if not p.is_absolute():
            p = self.roots[0] / p
        real = Path(os.path.realpath(p))  # follows symlinks, so links cannot escape a root
        if not self._inside(real):
            raise TargetError("path is outside the authorized project directories", 403)
        if must_exist and not real.exists():
            raise TargetError("not found", 404)
        return real

    def list(self, path: str) -> list[dict]:
        d = self.resolve(path)
        if not d.is_dir():
            raise TargetError("not a directory")
        out = []
        for e in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = e.lstat()
                out.append({"name": e.name, "dir": e.is_dir() and not e.is_symlink(), "size": st.st_size, "mtime": st.st_mtime,
                            "link": e.is_symlink()})
            except OSError:
                continue
        return out

    def read(self, path: str) -> bytes:
        f = self.resolve(path)
        if not f.is_file():
            raise TargetError("not a file")
        if f.stat().st_size > MAX_FILE:
            raise TargetError("file too large", 413)
        return f.read_bytes()

    def write(self, path: str, data: bytes) -> None:
        if len(data) > MAX_FILE:
            raise TargetError("file too large", 413)
        f = self.resolve(path, must_exist=False)
        f.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(str(f.parent))  # re-check after mkdir
        if f.is_symlink() or f.is_dir():
            raise TargetError("refusing to overwrite a link or directory", 403)
        tmp = f.with_name(f".{f.name}.cit-{uuid.uuid4().hex[:6]}")
        tmp.write_bytes(data)
        os.replace(tmp, f)

    def delete(self, path: str) -> None:
        f = self.resolve(path)
        if f.is_dir() or f.is_symlink():
            raise TargetError("only regular files can be deleted", 403)
        f.unlink()


# ------------------------------------------------------------------ tool detection
DETECT = {
    "git": ["git", "--version"], "node": ["node", "--version"], "npm": ["npm", "--version"],
    "python": ["python" if os.name == "nt" else "python3", "--version"],
    "claude": ["claude", "--version"], "docker": ["docker", "--version"], "java": ["java", "-version"],
    "tailscale": ["tailscale", "version"], "rustdesk": ["rustdesk", "--version"],
}


def detect_tools(names: list[str] | None = None) -> dict[str, dict]:
    res = {}
    for name in names or DETECT:
        argv = DETECT[name]
        path = shutil.which(argv[0])
        info = {"found": bool(path), "version": None}
        if path:
            try:
                p = subprocess.run([path, *argv[1:]], capture_output=True, text=True, timeout=8)
                txt = (p.stdout or p.stderr).strip().splitlines()
                info["version"] = txt[0][:80] if txt else None
            except (OSError, subprocess.TimeoutExpired):
                info["version"] = None
        res[name] = info
    return res


# ------------------------------------------------------------------ jobs
@dataclass
class Job:
    id: str
    argv: list[str]
    started: float = field(default_factory=time.time)
    out: bytearray = field(default_factory=bytearray)
    exit: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {"id": self.id, "output": self.out[since:].decode("utf-8", "replace"), "next": len(self.out),
                    "done": self.exit is not None, "exit": self.exit, "cancelled": self.cancelled, "timed_out": self.timed_out}


class JobManager:
    def __init__(self, guard: FsGuard):
        self.guard, self.jobs = guard, {}

    def start(self, argv: list[str], cwd: str | None, timeout: int, scrub: bool = True, extra_env: dict | None = None) -> Job:
        if not argv or not all(isinstance(a, str) and a and "\x00" not in a for a in argv) or len(argv) > 64:
            raise TargetError("argv must be a non-empty list of strings")
        if not (1 <= timeout <= 3600):
            raise TargetError("timeout must be 1..3600 seconds")
        wd = self.guard.resolve(cwd or str(self.guard.roots[0]))
        exe = shutil.which(argv[0], path=os.environ.get("PATH"))
        if not exe:
            raise TargetError(f"executable not found: {argv[0]}", 404)
        env = scrub_env() if scrub else dict(os.environ)
        env.update(extra_env or {})
        kw: dict = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        job = Job(uuid.uuid4().hex[:12], argv)
        job.proc = subprocess.Popen([exe, *argv[1:]], cwd=wd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, shell=False, **kw)
        self.jobs[job.id] = job
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        threading.Thread(target=self._watch, args=(job, timeout), daemon=True).start()
        return job

    def _pump(self, job: Job) -> None:
        assert job.proc and job.proc.stdout
        for chunk in iter(lambda: job.proc.stdout.read1(4096), b""):  # type: ignore[union-attr]
            with job.lock:
                if len(job.out) < MAX_OUTPUT:
                    job.out += chunk
        job.proc.wait()
        with job.lock:
            job.exit = job.proc.returncode

    def _watch(self, job: Job, timeout: int) -> None:
        try:
            job.proc.wait(timeout=timeout)  # type: ignore[union-attr]
        except subprocess.TimeoutExpired:
            job.timed_out = True
            self._kill(job)

    @staticmethod
    def _kill(job: Job) -> None:
        p = job.proc
        if not p or p.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=10)
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            p.kill()

    def get(self, jid: str) -> Job:
        j = self.jobs.get(jid)
        if not j:
            raise TargetError("job not found", 404)
        return j

    def cancel(self, jid: str) -> Job:
        j = self.get(jid)
        j.cancelled = True
        self._kill(j)
        return j

    def cancel_all(self) -> int:
        n = 0
        for j in self.jobs.values():
            if j.exit is None:
                j.cancelled = True; self._kill(j); n += 1
        return n


def claude_argv(prompt: str, permission_mode: str, allowed_tools: list[str]) -> list[str]:
    if permission_mode not in PERMISSION_MODES:
        raise TargetError(f"permission_mode must be one of {sorted(PERMISSION_MODES)}")
    if not prompt.strip() or len(prompt) > 20000:
        raise TargetError("prompt must be 1..20000 characters")
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:*()\- .,/]{0,80}", t) for t in allowed_tools) or len(allowed_tools) > 20:
        raise TargetError("invalid allowed_tools")
    argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", permission_mode]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    return argv


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except Exception as e:  # noqa: BLE001
        raise TargetError("invalid base64") from e
