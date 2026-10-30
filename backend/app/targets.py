"""Target interface: one API for 'a machine + project directories I may work in'.
Implementations: LocalTarget (this computer), AgentClient (portable agent), LinuxSshTarget (servers)."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .targetcore import FsGuard, Grants, JobManager, TargetError, claude_argv, detect_tools


class Target(Protocol):
    def info(self) -> dict: ...
    def fs_list(self, path: str) -> list[dict]: ...
    def fs_read(self, path: str) -> bytes: ...
    def fs_write(self, path: str, data: bytes) -> None: ...
    def fs_delete(self, path: str) -> None: ...
    def exec_start(self, argv: list[str], cwd: str | None, timeout: int) -> str: ...
    def claude_start(self, prompt: str, cwd: str, permission_mode: str, allowed_tools: list[str], timeout: int) -> str: ...
    def job(self, jid: str, since: int = 0) -> dict: ...
    def job_cancel(self, jid: str) -> dict: ...
    def disconnect(self) -> None: ...


class LocalTarget:
    """This computer, confined to explicit roots, with the same grant model as a remote agent."""
    kind = "local"

    def __init__(self, roots: list[Path | str], grants: Grants | None = None):
        self.guard = FsGuard(roots)
        self.jobs = JobManager(self.guard)
        self.grants = grants or Grants(terminal=True, write=True, claude=True)

    def info(self) -> dict:
        import platform
        return {"os": platform.system().lower(), "host": platform.node(), "roots": [str(r) for r in self.guard.roots],
                "grants": self.grants.names(), "tools": detect_tools()}

    def _need(self, g: str) -> None:
        if not getattr(self.grants, g):
            raise TargetError(f"'{g}' capability is not granted on this machine", 403)

    def fs_list(self, path):
        return self.guard.list(path)

    def fs_read(self, path):
        return self.guard.read(path)

    def fs_write(self, path, data):
        self._need("write"); self.guard.write(path, data)

    def fs_delete(self, path):
        self._need("write"); self.guard.delete(path)

    def exec_start(self, argv, cwd, timeout):
        self._need("terminal"); return self.jobs.start(argv, cwd, timeout).id

    def claude_start(self, prompt, cwd, permission_mode, allowed_tools, timeout):
        self._need("claude")
        # Runs with THIS machine's own Claude login. No credentials are ever passed in.
        return self.jobs.start(claude_argv(prompt, permission_mode, allowed_tools), cwd, timeout, scrub=False).id

    def job(self, jid, since=0):
        return self.jobs.get(jid).snapshot(since)

    def job_cancel(self, jid):
        return self.jobs.cancel(jid).snapshot()

    def disconnect(self) -> None:
        self.jobs.cancel_all()
