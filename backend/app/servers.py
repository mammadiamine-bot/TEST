"""Server manager: allowlisted Linux administration over argv (never a shell string), plus a Target for SSH servers.

Same philosophy as tools.py: fixed templates, validated params, risk classes, per-action approval for production.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .targetcore import MAX_FILE, TargetError, b64d  # noqa: F401
from .tools import Risk, ToolError

UNIT = re.compile(r"^[A-Za-z0-9_.@:-]{1,80}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
ABSPATH = re.compile(r"^/[A-Za-z0-9_./@+-]{1,200}$")


def _p(kind: str, v):
    if kind == "unit" and isinstance(v, str) and UNIT.match(v):
        return v
    if kind == "container" and isinstance(v, str) and CONTAINER.match(v):
        return v
    if kind == "host" and isinstance(v, str) and HOST.match(v) and not v.startswith("-"):
        return v
    if kind == "path" and isinstance(v, str) and ABSPATH.match(v) and ".." not in v.split("/"):
        return v
    if kind == "lines" and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000:
        return v
    raise ToolError(f"invalid {kind} parameter")


@dataclass(frozen=True)
class ServerTool:
    id: str
    title: str
    risk: Risk
    argv: tuple  # items are literals or "{param}" placeholders
    params: dict = field(default_factory=dict)  # name -> kind
    root: bool = False
    timeout_s: int = 60
    production: bool = False  # changes a running production service -> explicit per-action confirmation

    def build(self, supplied: dict | None) -> list[str]:
        supplied = supplied or {}
        if set(supplied) != set(self.params):
            raise ToolError(f"parameters must be exactly {sorted(self.params)}")
        vals = {k: _p(kind, supplied[k]) for k, kind in self.params.items()}
        out = []
        for a in self.argv:
            m = re.fullmatch(r"\{(\w+)\}", a)
            out.append(str(vals[m.group(1)]) if m else a)
        return (["sudo", "-n", "--"] if self.root else []) + out


SERVER_TOOLS: dict[str, ServerTool] = {t.id: t for t in [
    ServerTool("srv_uptime", "Uptime and load", Risk.READ_ONLY, ("uptime",)),
    ServerTool("srv_disk", "Disk usage", Risk.READ_ONLY, ("df", "-h")),
    ServerTool("srv_memory", "Memory usage", Risk.READ_ONLY, ("free", "-m")),
    ServerTool("srv_ports", "Listening ports", Risk.READ_ONLY, ("ss", "-tlnp")),
    ServerTool("srv_processes", "Top processes", Risk.READ_ONLY, ("ps", "aux", "--sort=-%mem")),
    ServerTool("srv_os", "OS release", Risk.READ_ONLY, ("cat", "/etc/os-release")),
    ServerTool("svc_status", "Service status", Risk.READ_ONLY, ("systemctl", "status", "{unit}", "--no-pager"), {"unit": "unit"}),
    ServerTool("svc_logs", "Service logs", Risk.READ_ONLY, ("journalctl", "-u", "{unit}", "-n", "{lines}", "--no-pager"), {"unit": "unit", "lines": "lines"}, root=True),
    ServerTool("docker_ps", "Docker containers", Risk.READ_ONLY, ("docker", "ps", "-a")),
    ServerTool("docker_logs", "Container logs", Risk.READ_ONLY, ("docker", "logs", "--tail", "{lines}", "{name}"), {"name": "container", "lines": "lines"}),
    ServerTool("nginx_test", "Test nginx configuration", Risk.READ_ONLY, ("nginx", "-t"), root=True),
    ServerTool("net_ping", "Ping a host", Risk.READ_ONLY, ("ping", "-c", "2", "-W", "2", "{host}"), {"host": "host"}, timeout_s=15),
    ServerTool("net_dns", "Resolve a name", Risk.READ_ONLY, ("getent", "hosts", "{host}"), {"host": "host"}, timeout_s=15),
    ServerTool("backup_create", "Back up a directory (tar.gz)", Risk.LOW, ("tar", "czf", "{dest}", "-C", "/", "{path}"), {"path": "path", "dest": "path"}, root=True, timeout_s=900),
    ServerTool("nginx_reload", "Reload nginx", Risk.LOW, ("systemctl", "reload", "nginx"), root=True, production=True),
    ServerTool("svc_restart", "Restart a service", Risk.HIGH, ("systemctl", "restart", "{unit}"), {"unit": "unit"}, root=True, production=True),
    ServerTool("svc_stop", "Stop a service", Risk.HIGH, ("systemctl", "stop", "{unit}"), {"unit": "unit"}, root=True, production=True),
    ServerTool("docker_restart", "Restart a container", Risk.HIGH, ("docker", "restart", "{name}"), {"name": "container"}, production=True),
    ServerTool("backup_restore", "Restore a backup archive", Risk.HIGH, ("tar", "xzf", "{src}", "-C", "/"), {"src": "path"}, root=True, timeout_s=900, production=True),
]}


def server_tool(tid: str) -> ServerTool:
    try:
        return SERVER_TOOLS[tid]
    except KeyError:
        raise ToolError(f"server action {tid!r} is not in the allowlist") from None


# ------------------------------------------------------------------ SSH target for Linux servers
def ssh_base(host: str, user: str, key: str, known_hosts: str, port: int, ssh_bin: str = "ssh") -> list[str]:
    return [ssh_bin, "-p", str(port), "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", f"{user}@{host}", "--"]


class LinuxSshTarget:
    """Target over OpenSSH for Linux. Every remote argument is shlex-quoted: ssh joins argv into one shell string."""
    kind = "ssh-linux"

    def __init__(self, host, user, key_path, known_hosts: Path, port=22, ssh_bin="ssh"):
        self.base = ssh_base(host, user, key_path, str(known_hosts), port, ssh_bin)
        self.jobs: dict[str, dict] = {}

    def _remote(self, argv: list[str], stdin: bytes | None = None, timeout: int = 60):
        return subprocess.run([*self.base, shlex.join(argv)], input=stdin, capture_output=True, timeout=timeout, shell=False)

    def info(self) -> dict:
        r = self._remote(["uname", "-sr"], timeout=15)
        if r.returncode != 0:
            raise TargetError(f"ssh failed: {r.stderr.decode(errors='replace')[-300:]}", 502)
        return {"os": "linux", "host": r.stdout.decode().strip(), "roots": ["/"], "grants": ["terminal", "write"], "tools": {}}

    def fs_list(self, path):
        r = self._remote(["ls", "-1Ap", "--", path], timeout=20)
        if r.returncode != 0:
            raise TargetError(r.stderr.decode(errors="replace")[-200:] or "ls failed")
        return [{"name": n.rstrip("/"), "dir": n.endswith("/"), "size": 0, "mtime": 0, "link": False} for n in r.stdout.decode().splitlines() if n]

    def fs_read(self, path):
        r = self._remote(["head", "-c", str(MAX_FILE), "--", path], timeout=60)
        if r.returncode != 0:
            raise TargetError(r.stderr.decode(errors="replace")[-200:] or "read failed", 404)
        return r.stdout

    def fs_write(self, path, data):
        r = self._remote(["sh", "-c", 'cat > "$1"', "sh", path], stdin=data, timeout=60)
        if r.returncode != 0:
            raise TargetError(r.stderr.decode(errors="replace")[-200:] or "write failed")

    def fs_delete(self, path):
        r = self._remote(["rm", "-f", "--", path], timeout=20)
        if r.returncode != 0:
            raise TargetError("delete failed")

    def exec_start(self, argv, cwd, timeout):
        full = (["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", cwd, *argv] if cwd else argv)
        jid = f"j{len(self.jobs) + 1}-{int(time.time())}"
        p = subprocess.Popen([*self.base, shlex.join(full)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, shell=False)
        job = {"p": p, "out": bytearray(), "t0": time.time(), "timeout": timeout, "cancelled": False}
        self.jobs[jid] = job
        import threading

        def pump():
            for ch in iter(lambda: p.stdout.read1(4096), b""):  # type: ignore[union-attr]
                job["out"] += ch
            p.wait()
        threading.Thread(target=pump, daemon=True).start()
        return jid

    def claude_start(self, *a, **k):
        raise TargetError("Claude Code is not run on SSH servers from this app (use the agent on a workstation, or central mode)", 501)

    def job(self, jid, since=0):
        j = self.jobs.get(jid)
        if not j:
            raise TargetError("job not found", 404)
        p = j["p"]
        timed = p.poll() is None and time.time() - j["t0"] > j["timeout"]
        if timed:
            p.kill(); j["cancelled"] = True
        rc = p.poll()
        return {"id": jid, "output": bytes(j["out"][since:]).decode("utf-8", "replace"), "next": len(j["out"]), "done": rc is not None,
                "exit": rc, "cancelled": j["cancelled"], "timed_out": timed}

    def job_cancel(self, jid):
        j = self.jobs.get(jid)
        if j:
            j["cancelled"] = True; j["p"].kill()
        return self.job(jid)

    def disconnect(self):
        for j in self.jobs.values():
            if j["p"].poll() is None:
                j["p"].kill()


def run_sync(target, argv: list[str], cwd: str | None = None, timeout: int = 120) -> dict:
    """Start a job on any target and wait for it. Returns {exit, output, timed_out}."""
    jid = target.exec_start(argv, cwd, timeout)
    since, out, end = 0, [], time.time() + timeout + 15
    while time.time() < end:
        j = target.job(jid, since)
        out.append(j["output"]); since = j["next"]
        if j["done"]:
            return {"exit": j["exit"], "output": "".join(out), "timed_out": j["timed_out"], "cancelled": j["cancelled"]}
        time.sleep(0.1)
    target.job_cancel(jid)
    return {"exit": None, "output": "".join(out), "timed_out": True, "cancelled": True}
