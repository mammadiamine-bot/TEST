"""Universal task engine: identify target -> verify authorization/connection -> detect tools -> choose Central or
Remote Claude -> plan -> approvals -> execute with live events -> verify -> report.

No free-text interpretation: the technician picks the task kind; free text is only the instruction handed to Claude Code.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import tools as winmod
from .db import new_id
from .dev import SKIP_DIRS, Dev
from .service import Core, CoreError
from .servers import SERVER_TOOLS, run_sync, server_tool
from .targetcore import _KV, _SECRET_VAL, TargetError, redact_argv
from .targets import LocalTarget
from .targetcore import Grants
from .tools import Risk, ToolError

KINDS = ("dev", "diagnose", "install_tools", "server", "deploy")
CLAUDE_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"]
INSTALLERS = {  # explicit, fixed argv per OS; never built from user text
    ("windows", "git"): ["winget", "install", "--id", "Git.Git", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
    ("windows", "node"): ["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
    ("windows", "python"): ["winget", "install", "--id", "Python.Python.3.12", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
    ("windows", "docker"): ["winget", "install", "--id", "Docker.DockerDesktop", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
    ("windows", "claude"): ["npm", "install", "-g", "@anthropic-ai/claude-code"],
    ("linux", "claude"): ["npm", "install", "-g", "@anthropic-ai/claude-code"],
    ("linux", "git"): ["sudo", "-n", "apt-get", "install", "-y", "git"], ("linux", "node"): ["sudo", "-n", "apt-get", "install", "-y", "nodejs", "npm"],
    ("linux", "python"): ["sudo", "-n", "apt-get", "install", "-y", "python3"],
}
INSTALLABLE = {"git", "node", "python", "docker", "claude"}
CLAUDE_HELP = ("Remote Claude needs, on THE CLIENT's computer: (1) the agent started with --allow-claude and --allow-write, (2) Claude Code installed "
               "(task kind 'Install tools'), (3) the client logged into Claude Code with their OWN account or key. Your personal Claude login is never copied.")


def clean(s: str) -> str:
    return _KV.sub(lambda m: m.group(1) + "=<redacted>", _SECRET_VAL.sub("<redacted>", s))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def claude_event(line: str) -> str | None:
    """Condense one stream-json line from `claude -p --output-format stream-json` into a log line."""
    try:
        j = json.loads(line)
    except json.JSONDecodeError:
        return line[:300] if line.strip() else None
    t = j.get("type")
    if t == "assistant":
        out = []
        for b in (j.get("message") or {}).get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append(b["text"].strip().replace("\n", " ")[:300])
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                out.append(f"tool {b.get('name')}: {inp.get('file_path') or inp.get('pattern') or inp.get('command') or ''}"[:200])
        return " | ".join(out) or None
    if t == "result":
        return f"result: {'ERROR ' if j.get('is_error') else ''}{str(j.get('result', ''))[:300]}" + (f" (cost ${j['total_cost_usd']:.4f})" if j.get("total_cost_usd") else "")
    return None


class Tasks:
    def __init__(self, core: Core, dev: Dev):
        self.core, self.dev, self.db = core, dev, core.db
        self.cancel_flags: set[str] = set()
        self.threads: dict[str, threading.Thread] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._central = None
        core.stop_hooks.append(self.cancel_all)

    # ---------------------------------------------------------------- central Claude (runs on THIS computer)
    def central(self) -> LocalTarget:
        if self._central is None:
            self._central = LocalTarget([self.core.s.data_dir / "workspaces"], Grants(claude=True))
            (self.core.s.data_dir / "workspaces").mkdir(parents=True, exist_ok=True)
        return self._central

    def central_available(self) -> bool:
        return bool(shutil.which("claude"))

    # ---------------------------------------------------------------- persistence
    def _save(self, t: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO tasks(id,device_id,project_id,kind,status,created_at,data) VALUES(?,?,?,?,?,?,?)",
                        (t["id"], t["device_id"], t.get("project_id"), t["kind"], t["status"], t["created_at"], json.dumps(t)))

    def get(self, tid: str) -> dict:
        r = self.db.one("SELECT data FROM tasks WHERE id=?", (tid,))
        if not r:
            raise CoreError("task not found", 404)
        return json.loads(r["data"])

    def list(self, did: str | None = None, kind: str | None = None) -> list[dict]:
        q, a = "SELECT data FROM tasks WHERE 1=1", []
        if did:
            q += " AND device_id=?"; a.append(did)
        if kind:
            q += " AND kind=?"; a.append(kind)
        out = []
        for r in self.db.all(q + " ORDER BY created_at DESC", tuple(a)):
            t = json.loads(r["data"]); t["events"] = t["events"][-5:]; out.append(t)
        return out

    def _ev(self, t: dict, msg: str, level="info") -> None:
        t["events"].append({"ts": time.time(), "level": level, "msg": clean(msg)[:600]})
        self._save(t)

    # ---------------------------------------------------------------- planning
    def _step(self, t: dict, typ: str, title: str, risk: str, auto: bool, **params) -> dict:
        production = bool(params.pop("production", False))
        s = {"id": f"s{len(t['steps']) + 1}", "type": typ, "title": title, "risk": risk, "auto": auto, "params": params,
             "status": "done_pending" if auto else "proposed", "approved_by": None, "approved_hash": None, "result": None,
             "production": production}
        t["steps"].append(s)
        return s

    def create(self, did: str, kind: str, instruction: str = "", project_id: str | None = None, mode: str = "auto",
               tools_wanted: list[str] | None = None, actions: list[dict] | None = None, allow_bash: bool = False) -> dict:
        if kind not in KINDS:
            raise CoreError(f"kind must be one of {KINDS}", 422)
        if mode not in ("auto", "central", "remote"):
            raise CoreError("mode must be auto, central or remote", 422)
        dev = self.core.device(did)
        target = self.core.target(did)  # raises 403/409 unless the device is authorized AND connected
        try:
            info = target.info()  # connection verification + tool detection
        except (TargetError, Exception) as e:  # noqa: BLE001
            raise CoreError(f"cannot reach the target: {e}", 502)
        found = {k: v for k, v in (info.get("tools") or {}).items()}
        t = {"id": new_id("task"), "device_id": did, "project_id": project_id, "kind": kind, "instruction": instruction[:4000], "status": "planned",
             "created_at": time.time(), "mode": None, "mode_reason": "", "allow_bash": allow_bash,
             "target": {"name": dev["name"], "os": info.get("os"), "grants": info.get("grants"), "tools": {k: v.get("found") for k, v in found.items()}},
             "steps": [], "events": [], "changes": [], "report": None, "diagnose_plan_id": None}
        proj = None
        if kind in ("dev", "deploy"):
            if not project_id:
                raise CoreError("choose a project", 422)
            proj = self.dev.project(project_id)
            if proj["device_id"] != did:
                raise CoreError("that project belongs to a different device", 403)  # cross-client isolation
        self._ev(t, f"Target: {dev['name']} ({info.get('os')}); authorized and connected; grants={info.get('grants')}")
        getattr(self, f"_plan_{kind}")(t, dev, info, proj, mode, tools_wanted or [], actions or [])
        self._save(t)
        self.db.audit("technician", "task.created", did, task=t["id"], kind=kind, mode=t["mode"], status=t["status"])
        return t

    def _plan_dev(self, t, dev, info, proj, mode, _tools, _actions):
        remote_ok = "claude" in (info.get("grants") or []) and (info.get("tools") or {}).get("claude", {}).get("found") and dev["transport"] != "local"
        central_ok = self.central_available()
        if mode == "remote" and not remote_ok:
            t["status"], t["mode_reason"] = "blocked", "Remote Claude is not available on this target. " + CLAUDE_HELP
        elif mode == "central" and not central_ok:
            t["status"], t["mode_reason"] = "blocked", "Claude Code is not installed on THIS computer. Install it and sign in, then retry."
        elif mode == "auto":
            if remote_ok:
                t["mode"], t["mode_reason"] = "remote", "Claude Code is installed on the target and the owner allowed it to run there."
            elif central_ok:
                t["mode"], t["mode_reason"] = "central", "Running Claude Code on this computer against a local copy of the project."
            else:
                t["status"], t["mode_reason"] = "blocked", "No Claude Code available (neither on this computer nor on the target). " + CLAUDE_HELP
        else:
            t["mode"], t["mode_reason"] = mode, "Chosen by the technician."
        if t["status"] == "blocked":
            self._ev(t, t["mode_reason"], "warn"); return
        self._ev(t, f"Execution mode: {t['mode']} — {t['mode_reason']}")
        self._step(t, "detect", "Detect tools and verify connection", "read_only", True)
        if t["mode"] == "central":
            self._step(t, "pull", "Copy project files from the target to a local workspace", "read_only", True)
            self._step(t, "claude", "Run Claude Code on this computer (edits only the local copy)", "low", False)
            self._step(t, "review", "Compute the change set", "read_only", True)
            self._step(t, "push", "Write approved changes to the target (originals backed up first)", "low", False)
        else:
            self._step(t, "claude_remote", "Run Claude Code ON THE TARGET (edits client files directly; project backed up first)", "high", False)
        self._step(t, "verify", "Run the project's tests on the target", "low", False)

    def _plan_diagnose(self, t, dev, info, proj, mode, *_):
        if dev["transport"] not in ("mock", "agent"):
            t["status"] = "blocked"; t["mode_reason"] = "Windows diagnostics run on mock devices or through the agent."; return
        t["mode"] = "central"; t["mode_reason"] = "Deterministic diagnostics (not AI) with approval-gated repairs."
        self._step(t, "diagnose", "Run all diagnostic modules", "read_only", True)
        self._step(t, "make_plan", "Create the repair plan (approve steps on the Repairs page)", "read_only", True)

    def _plan_install_tools(self, t, dev, info, proj, mode, wanted, _a):
        if not wanted or not set(wanted) <= INSTALLABLE:
            raise CoreError(f"tools must be a subset of {sorted(INSTALLABLE)}", 422)
        os_ = info.get("os")
        t["mode"], t["mode_reason"] = "central", "Fixed installer commands run through the agent after your approval."
        self._step(t, "detect", "Detect tools", "read_only", True)
        for name in wanted:
            if (info.get("tools") or {}).get(name, {}).get("found"):
                self._ev(t, f"{name} is already installed"); continue
            argv = INSTALLERS.get((os_, name))
            if not argv:
                self._ev(t, f"No installer recipe for {name} on {os_}", "warn"); continue
            self._step(t, "install_tool", f"Install {name}: {' '.join(argv)}", "high", False, tool=name, argv=argv)
        if len(t["steps"]) == 1:
            t["status"] = "completed"; t["steps"][0]["status"] = "done"; t["report"] = "Nothing to install."

    def _plan_server(self, t, dev, info, proj, mode, _t, actions):
        if not actions:
            raise CoreError("choose at least one server action", 422)
        t["mode"], t["mode_reason"] = "central", "Allowlisted server actions over SSH."
        for a in actions:
            st = server_tool(a.get("tool_id", ""))
            argv = st.build(a.get("params") or {})  # validates now
            self._step(t, "server_action", st.title, st.risk.value, st.risk == Risk.READ_ONLY, tool_id=st.id, params=a.get("params") or {}, argv=argv,
                       production=st.production)

    def _plan_deploy(self, t, dev, info, proj, mode, *_):
        if not proj["deploy_cmd"]:
            raise CoreError("this project has no deploy command configured", 409)
        t["mode"], t["mode_reason"] = "central", "Deployment of the project's configured command."
        self._step(t, "backup", "Back up the project before deploying", "low", False)
        self._step(t, "deploy", f"DEPLOY: {' '.join(proj['deploy_cmd'])}", "high", False, argv=proj["deploy_cmd"], production=True)
        self._step(t, "verify", "Run the project's tests after deploying", "low", False)

    # ---------------------------------------------------------------- approvals
    def _hash(self, t: dict, s: dict) -> str:
        return _sha(json.dumps([t["device_id"], t["id"], s["type"], s["params"], t["instruction"] if s["type"].startswith("claude") else ""], sort_keys=True).encode())

    def approve(self, tid: str, sid: str, approver: str, confirm_high_risk=False, confirm_text: str | None = None) -> dict:
        t = self.get(tid)
        s = next((x for x in t["steps"] if x["id"] == sid), None)
        if not s:
            raise CoreError("step not found", 404)
        if s["status"] != "proposed":
            raise CoreError(f"step is {s['status']}", 409)
        if s["risk"] == "high" and not confirm_high_risk:
            raise CoreError("high-risk step needs explicit per-action confirmation", 428)
        if s.get("production"):
            name = (self.dev.project(t["project_id"])["name"] if t.get("project_id") else t["target"]["name"])
            if (confirm_text or "").strip() != name:
                raise CoreError(f"production change: type the exact name '{name}' to confirm", 428)
        s.update(status="approved", approved_by=approver, approved_hash=self._hash(t, s))
        t["status"] = "approved" if t["status"] in ("planned", "awaiting_approval") else t["status"]
        self._ev(t, f"{approver} approved '{s['title']}' ({s['risk']})")
        self.db.audit(approver, "task.step_approved", t["device_id"], task=tid, step=sid, risk=s["risk"])
        return self.get(tid)

    def approve_low_risk(self, tid: str, approver: str) -> dict:
        for s in self.get(tid)["steps"]:
            if s["status"] == "proposed" and s["risk"] == "low" and not s.get("production"):
                self.approve(tid, s["id"], approver)
        return self.get(tid)

    def reject(self, tid: str, sid: str) -> dict:
        t = self.get(tid)
        for s in t["steps"]:
            if s["id"] == sid and s["status"] in ("proposed", "approved"):
                s["status"] = "rejected"
        self._ev(t, f"step {sid} rejected")
        return t

    # ---------------------------------------------------------------- execution
    def execute(self, tid: str, background: bool = True) -> dict:
        t = self.get(tid)
        if t["status"] in ("blocked", "completed", "cancelled", "running"):
            raise CoreError(f"task is {t['status']}", 409)
        lock = self.locks.setdefault(t["device_id"], threading.Lock())
        if background:
            if not lock.acquire(blocking=False):
                raise CoreError("another task is running on this device", 409)
            th = threading.Thread(target=self._guarded, args=(tid, lock), daemon=True)
            self.threads[tid] = th
            t["status"] = "running"; self._save(t)
            th.start()
            return t
        if not lock.acquire(blocking=False):
            raise CoreError("another task is running on this device", 409)
        self._guarded(tid, lock)
        return self.get(tid)

    def wait(self, tid: str, timeout: float = 60) -> dict:
        th = self.threads.get(tid)
        if th:
            th.join(timeout)
        return self.get(tid)

    def _guarded(self, tid: str, lock: threading.Lock) -> None:
        t = self.get(tid)
        try:
            self._run(t)
        except Exception as e:  # noqa: BLE001
            t = self.get(tid)
            t["status"] = "failed"; self._ev(t, f"internal error: {type(e).__name__}: {e}", "error")
            self.db.audit("system", "task.error", t["device_id"], task=tid, error=type(e).__name__)
        finally:
            self.cancel_flags.discard(tid)
            lock.release()

    def cancel(self, tid: str) -> dict:
        t = self.get(tid)
        self.cancel_flags.add(tid)
        if t["status"] not in ("running",):
            t["status"] = "cancelled"; self._ev(t, "cancelled by technician", "warn"); self.cancel_flags.discard(tid)
        self.db.audit("technician", "task.cancel", t["device_id"], task=tid)
        return self.get(tid)

    def cancel_all(self) -> None:
        for r in self.db.all("SELECT id FROM tasks WHERE status='running'"):
            self.cancel_flags.add(r["id"])

    def _cancelled(self, t: dict) -> bool:
        return t["id"] in self.cancel_flags

    def _run(self, t: dict) -> None:
        t["status"] = "running"; self._save(t)
        did = t["device_id"]
        failed = False
        for s in t["steps"]:
            if s["status"] in ("done", "verified", "rejected", "failed", "skipped", "unverified"):
                continue
            if self._cancelled(t):
                t["status"] = "cancelled"; self._ev(t, "task cancelled", "warn"); return
            if not s["auto"]:
                if s["status"] != "approved":
                    t["status"] = "awaiting_approval"; self._ev(t, f"waiting for approval: {s['title']}")
                    return
                if self._hash(t, s) != s["approved_hash"]:
                    s["status"], s["result"] = "failed", {"ok": False, "detail": "step changed after approval"}; failed = True; break
            self._ev(t, f"▶ {s['title']}")
            try:
                target = self.core.target(did, "repair" if s["risk"] != "read_only" else "diagnose")  # re-checks session/expiry every step
                res = getattr(self, f"_do_{s['type']}")(t, s, target)
            except (CoreError, TargetError, ToolError) as e:
                res = {"ok": False, "verified": False, "detail": str(e)}
            s["result"] = res
            s["status"] = res.get("status") or ("verified" if res.get("verified") else ("done" if res.get("ok") else "failed"))
            self._ev(t, f"{'✔' if res.get('ok') else '✖'} {s['title']}: {res.get('detail', '')}", "info" if res.get("ok") else "error")
            self.db.audit("technician", "task.step", did, task=t["id"], step=s["id"], type=s["type"], ok=res.get("ok"), verified=res.get("verified"))
            if not res.get("ok"):
                failed = True; break
        if failed:
            for s in t["steps"]:
                if s["status"] in ("proposed", "approved", "done_pending"):
                    s["status"] = "skipped"
        ran = [s for s in t["steps"] if s["status"] in ("done", "verified", "unverified", "failed")]
        unverified = any(s["status"] in ("unverified",) or (s["type"] in ("verify",) and s["status"] != "verified") for s in ran)
        t["status"] = "cancelled" if self._cancelled(t) else "failed" if failed else ("completed_with_issues" if unverified else "completed")
        t["report"] = self.report(t)
        self._save(t)
        self.db.audit("technician", "task.finished", did, task=t["id"], status=t["status"])

    # ---- step implementations: each returns {ok, verified?, detail, status?}
    def _do_detect(self, t, s, target):
        info = target.info()
        t["target"]["tools"] = {k: v.get("found") for k, v in (info.get("tools") or {}).items()}
        return {"ok": True, "detail": ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in t["target"]["tools"].items() if k in ("git", "node", "python", "claude", "docker"))}

    def _ws(self, t) -> Path:
        d = self.core.s.data_dir / "workspaces" / t["device_id"] / t["project_id"]
        return d

    def _do_pull(self, t, s, target):
        proj = self.dev.project(t["project_id"])
        ws = self._ws(t)
        shutil.rmtree(ws, ignore_errors=True)
        files = self.dev._walk(target, proj["root"])
        for rel, _ in files:
            data = target.fs_read(self.dev.join(proj["root"], rel))
            for sub in ("base", "work"):
                f = ws / sub / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data)
        (ws / "work").mkdir(parents=True, exist_ok=True)
        return {"ok": True, "detail": f"{len(files)} files copied"}

    def _stream(self, t, target, jid, timeout) -> dict:
        since, buf, end, lines = 0, "", time.time() + timeout, 0
        while time.time() < end:
            if self._cancelled(t):
                target.job_cancel(jid); return {"cancelled": True, "exit": None, "lines": lines}
            j = target.job(jid, since)
            since = j["next"]; buf += j["output"]
            *done, buf = buf.split("\n")
            for ln in done:
                ev = claude_event(ln)
                if ev:
                    lines += 1; self._ev(t, ev)
            if j["done"]:
                if buf.strip():
                    ev = claude_event(buf)
                    if ev:
                        self._ev(t, ev)
                return {"exit": j["exit"], "cancelled": j["cancelled"], "timed_out": j["timed_out"], "lines": lines}
            time.sleep(0.2)
        target.job_cancel(jid)
        return {"exit": None, "timed_out": True, "lines": lines}

    def _prompt(self, t) -> str:
        return (t["instruction"] + "\n\nRules: work only inside the current directory; do not read or print secrets, tokens or credentials; "
                "do not delete files unless asked; explain what you changed at the end.")

    def _tools(self, t) -> list[str]:
        return CLAUDE_TOOLS + (["Bash"] if t.get("allow_bash") else [])

    def _do_claude(self, t, s, target):
        c = self.central()
        work = str(self._ws(t) / "work")
        jid = c.claude_start(self._prompt(t), work, "acceptEdits", self._tools(t), 1800)
        r = self._stream(t, c, jid, 1800)
        if r.get("cancelled"):
            return {"ok": False, "detail": "cancelled"}
        ok = r.get("exit") == 0
        return {"ok": ok, "detail": f"Claude Code exited {r.get('exit')}" + (" (timed out)" if r.get("timed_out") else "")}

    def _do_review(self, t, s, target):
        base, work = self._ws(t) / "base", self._ws(t) / "work"
        names = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()} | {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()}
        changes = []
        for n in sorted(names):
            b, w = base / n, work / n
            if b.exists() and not w.exists():
                changes.append({"path": n, "status": "deleted", "diff": ""})
            elif not b.exists():
                changes.append({"path": n, "status": "added", "diff": self._diff(b"", w.read_bytes(), n)})
            elif b.read_bytes() != w.read_bytes():
                changes.append({"path": n, "status": "modified", "diff": self._diff(b.read_bytes(), w.read_bytes(), n)})
        t["changes"] = changes
        if any(c["status"] == "deleted" for c in changes):
            for st in t["steps"]:
                if st["type"] == "push" and st["status"] == "proposed":
                    st["risk"] = "high"; st["title"] += " — INCLUDES FILE DELETIONS"
        if not changes:
            for st in t["steps"]:
                if st["type"] == "push":
                    st["status"] = "skipped"
        return {"ok": True, "detail": f"{len(changes)} file(s) changed: " + ", ".join(f"{c['status']} {c['path']}" for c in changes[:8])}

    @staticmethod
    def _diff(a: bytes, b: bytes, name: str) -> str:
        try:
            return "".join(list(difflib.unified_diff(a.decode().splitlines(True), b.decode().splitlines(True), f"a/{name}", f"b/{name}"))[:400])
        except UnicodeDecodeError:
            return "(binary file)"

    def _do_push(self, t, s, target):
        proj = self.dev.project(t["project_id"])
        work = self._ws(t) / "work"
        bdir = self.core.s.data_dir / "backups" / "tasks" / t["id"]
        manifest = {}
        for c in t["changes"]:
            path = self.dev.join(proj["root"], c["path"])
            try:
                old = target.fs_read(path)
                manifest[c["path"]] = {"existed": True}
                f = bdir / c["path"]; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(old)
            except TargetError:
                manifest[c["path"]] = {"existed": False}
        (bdir).mkdir(parents=True, exist_ok=True)
        (bdir / "manifest.json").write_text(json.dumps(manifest))
        for c in t["changes"]:
            path = self.dev.join(proj["root"], c["path"])
            if c["status"] == "deleted":
                target.fs_delete(path)
            else:
                target.fs_write(path, (work / c["path"]).read_bytes())
        # verification: read everything back from the target
        bad = []
        for c in t["changes"]:
            path = self.dev.join(proj["root"], c["path"])
            if c["status"] == "deleted":
                try:
                    target.fs_read(path); bad.append(c["path"])
                except TargetError:
                    pass
            elif target.fs_read(path) != (work / c["path"]).read_bytes():
                bad.append(c["path"])
        if bad:
            return {"ok": False, "detail": f"read-back mismatch: {bad}"}
        return {"ok": True, "verified": True, "detail": f"{len(t['changes'])} file(s) written and read back identical; originals saved for rollback"}

    def rollback(self, tid: str) -> dict:
        """Undo a pushed central-mode change: restore originals, remove files the task created."""
        t = self.get(tid)
        mf = self.core.s.data_dir / "backups" / "tasks" / tid / "manifest.json"
        if not mf.exists():
            raise CoreError("this task has nothing to roll back (remote tasks use the project backup)", 409)
        proj = self.dev.project(t["project_id"])
        target = self.core.target(t["device_id"], "repair")
        manifest = json.loads(mf.read_text())
        for rel, m in manifest.items():
            path = self.dev.join(proj["root"], rel)
            if m["existed"]:
                target.fs_write(path, (mf.parent / rel).read_bytes())
            else:
                try:
                    target.fs_delete(path)
                except TargetError:
                    pass
        self.db.audit("technician", "task.rollback", t["device_id"], task=tid, files=len(manifest))
        self._ev(t, f"rolled back {len(manifest)} file(s)", "warn")
        return {"restored": len(manifest)}

    def _do_claude_remote(self, t, s, target):
        proj = self.dev.project(t["project_id"])
        b = self.dev.backup(proj["id"], f"pre-{t['id']}")
        self._ev(t, f"project backed up ({b['files']} files, id {b['id']})")
        jid = target.claude_start(self._prompt(t), proj["root"], "acceptEdits", self._tools(t), 1800)
        t["remote_job"] = jid; self._save(t)
        r = self._stream(t, target, jid, 1800)
        if r.get("cancelled"):
            return {"ok": False, "detail": "cancelled"}
        return {"ok": r.get("exit") == 0, "detail": f"remote Claude Code exited {r.get('exit')}" + (" (timed out)" if r.get("timed_out") else "") + f"; backup {b['id']}"}

    def _do_verify(self, t, s, target):
        proj = self.dev.project(t["project_id"])
        if not proj["test_cmd"]:
            return {"ok": True, "verified": False, "status": "unverified", "detail": "no test command configured — result NOT verified"}
        r = run_sync(target, proj["test_cmd"], proj["root"], 1800)
        tail = r["output"].strip().splitlines()[-3:]
        ok = r["exit"] == 0
        return {"ok": ok, "verified": ok, "detail": f"{' '.join(proj['test_cmd'])} -> exit {r['exit']}; " + " / ".join(tail)[:200]}

    def _do_backup(self, t, s, target):
        b = self.dev.backup(t["project_id"], f"pre-{t['id']}")
        return {"ok": True, "detail": f"backup {b['id']} ({b['files']} files)"}

    def _do_deploy(self, t, s, target):
        proj = self.dev.project(t["project_id"])
        r = run_sync(target, s["params"]["argv"], proj["root"], 1800)
        for ln in r["output"].splitlines()[-20:]:
            self._ev(t, ln)
        return {"ok": r["exit"] == 0, "detail": f"deploy command exit {r['exit']}"}

    def _do_install_tool(self, t, s, target):
        name, argv = s["params"]["tool"], s["params"]["argv"]
        r = run_sync(target, argv, None, 1800)
        info = target.info()
        found = (info.get("tools") or {}).get(name, {}).get("found")
        return {"ok": r["exit"] == 0 and bool(found), "verified": bool(found),
                "detail": f"installer exit {r['exit']}; {name} now {'detected' if found else 'NOT detected (a new terminal/PATH may be needed)'}"}

    def _do_server_action(self, t, s, target):
        r = run_sync(target, s["params"]["argv"], None, server_tool(s["params"]["tool_id"]).timeout_s)
        out = r["output"].strip()
        for ln in out.splitlines()[:30]:
            self._ev(t, ln)
        return {"ok": r["exit"] == 0, "verified": r["exit"] == 0, "detail": f"exit {r['exit']}"}

    def _do_diagnose(self, t, s, target):
        run = self.core.diagnose(t["device_id"])
        t["diagnose_run_id"] = run["id"]
        return {"ok": True, "detail": f"{len(run['findings'])} findings, {len(run['errors'])} module errors"}

    def _do_make_plan(self, t, s, target):
        p = self.core.create_plan(t["device_id"], t["diagnose_run_id"])
        t["diagnose_plan_id"] = p["id"]
        return {"ok": True, "detail": f"plan {p['id']} with {len(p['steps'])} proposed steps — approve on the Repairs page"}

    # ---------------------------------------------------------------- report
    def report(self, t: dict) -> str:
        L = [f"# Task report — {t['kind']} ({t['id']})", "", f"- Target: **{t['target']['name']}** ({t['target']['os']})",
             f"- Execution mode: **{t['mode']}** — {t['mode_reason']}", f"- Status: **{t['status']}**", f"- Instruction: {t['instruction'] or '—'}", "",
             "## Steps", "| Step | Risk | Approved by | Outcome |", "|---|---|---|---|"]
        for s in t["steps"]:
            r = s.get("result") or {}
            L.append(f"| {s['title']} | {s['risk']} | {s.get('approved_by') or ('auto' if s['auto'] else '—')} | {s['status']}: {r.get('detail', '')} |")
        if t["changes"]:
            L += ["", "## Changes"] + [f"- {c['status']}: `{c['path']}`" for c in t["changes"]]
        unver = [s for s in t["steps"] if s["status"] == "unverified"]
        L += ["", "## Verification", "All verifiable steps passed their checks." if not unver and t["status"] == "completed"
              else "**Not everything was verified** — see steps marked unverified/failed above."]
        L += ["", "_Generated by Claude IT Technician. AI output is Claude Code's own; the plan, approvals and verification are deterministic._"]
        return "\n".join(L)
