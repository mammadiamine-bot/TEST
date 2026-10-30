"""Development workspace: projects on any authorized target — explorer, editor, git, build/test, terminal, backup/rollback."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path, PurePosixPath

from .db import new_id
from .service import Core, CoreError
from .servers import run_sync
from .targetcore import SKIP_DIRS, TargetError, redact_argv

STACKS = {  # suggestions only; the technician confirms or edits the commands
    "react": (["npm", "run", "build"], ["npm", "test"]), "nextjs": (["npm", "run", "build"], ["npm", "test"]),
    "vue": (["npm", "run", "build"], ["npm", "test"]), "node": (["npm", "run", "build"], ["npm", "test"]),
    "typescript": (["npm", "run", "build"], ["npm", "test"]), "python": (None, ["python", "-m", "pytest", "-q"]),
    "fastapi": (None, ["python", "-m", "pytest", "-q"]), "django": (None, ["python", "manage.py", "test"]),
    "java": (["./gradlew", "build"], ["./gradlew", "test"]), "kotlin": (["./gradlew", "build"], ["./gradlew", "test"]),
    "android": (["./gradlew", "assembleDebug"], ["./gradlew", "test"]), "flutter": (["flutter", "build", "apk"], ["flutter", "test"]),
    "docker": (["docker", "build", "."], None), "desktop": (None, None), "legacy": (None, None), "database": (None, None), "other": (None, None),
}
MAX_FILES, MAX_BYTES = 3000, 50 * 1024 * 1024
GIT = {"status": ["git", "status", "--porcelain=v1", "-b"], "diff": ["git", "diff", "--stat"], "log": ["git", "log", "--oneline", "-n", "20"],
       "branch": ["git", "branch", "-a"]}


def _cmd(v):
    if v is None:
        return None
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x and len(x) < 300 for x in v) or len(v) > 30:
        raise CoreError("commands must be argument lists, e.g. [\"npm\",\"test\"]", 422)
    return v


class Dev:
    def __init__(self, core: Core):
        self.core, self.db = core, core.db

    # ---- helpers
    def join(self, root: str, rel: str) -> str:
        rel = (rel or "").replace("\\", "/").strip("/")
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            raise CoreError("path must be relative to the project", 422)
        sep = "\\" if ("\\" in root or re.match(r"^[A-Za-z]:", root)) else "/"
        return root.rstrip("/\\") + (sep + rel.replace("/", sep) if rel else "")

    def _row(self, pid: str) -> dict:
        r = self.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not r:
            raise CoreError("project not found", 404)
        for k in ("build_cmd", "test_cmd", "deploy_cmd"):
            r[k] = json.loads(r[k]) if r[k] else None
        return r

    def project(self, pid: str) -> dict:
        return self._row(pid)

    def projects(self, did: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT id FROM projects" + (" WHERE device_id=?" if did else "") + " ORDER BY created_at DESC", (did,) if did else ())
        return [self._row(r["id"]) for r in rows]

    def _t(self, p: dict, need="diagnose"):
        return self.core.target(p["device_id"], need)

    # ---- projects
    def create(self, did: str, name: str, root: str, stack: str, build_cmd=None, test_cmd=None, deploy_cmd=None, git_url=None) -> dict:
        if stack not in STACKS:
            raise CoreError(f"stack must be one of {sorted(STACKS)}", 422)
        t = self.core.target(did)
        try:
            t.fs_list(root)  # proves the directory exists AND is inside what the owner authorized
        except TargetError as e:
            raise CoreError(f"project directory is not accessible: {e}", e.status)
        if build_cmd is None and test_cmd is None:
            build_cmd, test_cmd = STACKS[stack]
        pid = new_id("prj")
        self.db.execute("INSERT INTO projects(id,device_id,name,root,stack,build_cmd,test_cmd,deploy_cmd,git_url,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (pid, did, name, root, stack, json.dumps(_cmd(build_cmd)), json.dumps(_cmd(test_cmd)), json.dumps(_cmd(deploy_cmd)), git_url, time.time()))
        self.db.audit("technician", "project.created", did, project=pid, name=name, root=root)
        return self._row(pid)

    def tree(self, pid: str, rel: str = "") -> list[dict]:
        p = self._row(pid)
        try:
            return self._t(p).fs_list(self.join(p["root"], rel))
        except TargetError as e:
            raise CoreError(str(e), e.status)

    def read(self, pid: str, rel: str) -> dict:
        p = self._row(pid)
        try:
            b = self._t(p).fs_read(self.join(p["root"], rel))
        except TargetError as e:
            raise CoreError(str(e), e.status)
        try:
            return {"binary": False, "content": b.decode("utf-8"), "size": len(b)}
        except UnicodeDecodeError:
            return {"binary": True, "content": "", "size": len(b)}

    def write(self, pid: str, rel: str, content: str) -> dict:
        p = self._row(pid)
        t = self._t(p, "repair")
        path = self.join(p["root"], rel)
        try:
            try:
                old = t.fs_read(path)
            except TargetError:
                old = None
            bdir = self.core.s.data_dir / "backups" / "edits" / pid
            bdir.mkdir(parents=True, exist_ok=True)
            if old is not None:  # every editor save keeps the previous version
                (bdir / f"{int(time.time()*1000)}-{hashlib.sha1(rel.encode()).hexdigest()[:8]}.bak").write_bytes(old)
            t.fs_write(path, content.encode())
        except TargetError as e:
            raise CoreError(str(e), e.status)
        self.db.audit("technician", "file.written", p["device_id"], project=pid, path=rel, bytes=len(content))
        return {"ok": True}

    # ---- git / build / test / terminal
    def git(self, pid: str, what: str) -> dict:
        if what not in GIT:
            raise CoreError(f"git action must be one of {sorted(GIT)}", 422)
        p = self._row(pid)
        return self._run(p, GIT[what], 30, f"git.{what}")

    def command(self, pid: str, which: str) -> dict:
        p = self._row(pid)
        argv = p.get(f"{which}_cmd")
        if which not in ("build", "test") or not argv:
            raise CoreError(f"no {which} command configured for this project", 409)
        return self._run(p, argv, 1800, which)

    def terminal(self, pid: str, argv: list[str], timeout: int = 300) -> dict:
        p = self._row(pid)
        return self._run(p, _cmd(argv), timeout, "terminal")

    def _run(self, p: dict, argv: list[str], timeout: int, label: str) -> dict:
        t = self._t(p, "repair" if label not in ("git.status", "git.diff", "git.log", "git.branch") else "diagnose")
        t0 = time.time()
        try:
            r = run_sync(t, argv, p["root"], timeout)
        except TargetError as e:
            self.db.audit("technician", "dev.run_denied", p["device_id"], project=p["id"], argv=redact_argv(argv), reason=str(e))
            raise CoreError(str(e), e.status)
        r["duration_ms"] = int((time.time() - t0) * 1000)
        self.db.audit("technician", "dev.run", p["device_id"], project=p["id"], label=label, argv=redact_argv(argv), exit=r["exit"], ms=r["duration_ms"])
        return r

    def environment(self, pid: str) -> dict:
        p = self._row(pid)
        i = self._t(p).info()
        return {"os": i.get("os"), "host": i.get("host"), "grants": i.get("grants"), "tools": i.get("tools"), "roots": i.get("roots"), "stack": p["stack"]}

    # ---- backup / rollback
    def _walk(self, t, root: str, rel: str = "", acc=None):
        acc = acc if acc is not None else []
        for e in t.fs_list(self.join(root, rel)):
            r = f"{rel}/{e['name']}".lstrip("/")
            if e["dir"]:
                if e["name"] not in SKIP_DIRS and not e.get("link"):
                    self._walk(t, root, r, acc)
            elif not e.get("link"):
                acc.append((r, e["size"]))
            if len(acc) > MAX_FILES:
                raise CoreError(f"project has more than {MAX_FILES} files (excluding build/vendor folders)", 413)
        return acc

    def backup(self, pid: str, label: str = "manual") -> dict:
        p = self._row(pid)
        t = self._t(p)
        files = self._walk(t, p["root"])
        if sum(s for _, s in files) > MAX_BYTES:
            raise CoreError("project is larger than 50 MB (excluding build/vendor folders)", 413)
        bid = f"{int(time.time())}-{label}"
        d = self.core.s.data_dir / "backups" / "projects" / pid / bid
        for rel, _ in files:
            f = d / "files" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(t.fs_read(self.join(p["root"], rel)))
        (d / "manifest.json").write_text(json.dumps({"files": [r for r, _ in files], "created": time.time()}))
        self.db.audit("technician", "project.backup", p["device_id"], project=pid, backup=bid, files=len(files))
        return {"id": bid, "files": len(files)}

    def backups(self, pid: str) -> list[dict]:
        d = self.core.s.data_dir / "backups" / "projects" / pid
        out = []
        for m in sorted(d.glob("*/manifest.json"), reverse=True) if d.exists() else []:
            j = json.loads(m.read_text()); out.append({"id": m.parent.name, "files": len(j["files"]), "created": j["created"]})
        return out

    def rollback(self, pid: str, bid: str) -> dict:
        """Restores every file in the backup to its saved content. Files created after the backup are left alone."""
        if not re.fullmatch(r"[0-9]+-[A-Za-z0-9_-]+", bid):
            raise CoreError("invalid backup id", 422)
        p = self._row(pid)
        t = self._t(p, "repair")
        d = self.core.s.data_dir / "backups" / "projects" / pid / bid
        if not (d / "manifest.json").exists():
            raise CoreError("backup not found", 404)
        files = json.loads((d / "manifest.json").read_text())["files"]
        n = 0
        for rel in files:
            data = (d / "files" / rel).read_bytes()
            try:
                if t.fs_read(self.join(p["root"], rel)) != data:
                    t.fs_write(self.join(p["root"], rel), data); n += 1
            except TargetError:
                t.fs_write(self.join(p["root"], rel), data); n += 1
        self.db.audit("technician", "project.rollback", p["device_id"], project=pid, backup=bid, restored=n)
        return {"restored": n, "checked": len(files)}
