"""Routes for the universal workspace: connections, projects, tasks, servers, files, emergency stop."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import connections as C
from .dev import STACKS, Dev
from .features import matrix
from .netpolicy import NetworkPolicyError
from .security import require_token
from .servers import SERVER_TOOLS, run_sync, server_tool
from .service import Core, CoreError
from .targetcore import TargetError, b64d, b64e, redact_argv
from .tasks import KINDS, Tasks
from .tools import Risk


class ProbeIn(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    allow_public: bool = False


class RustDeskIn(BaseModel):
    remote_id: str = Field(max_length=40)


class GitIn(BaseModel):
    url: str = Field(max_length=500)


class ProjectIn(BaseModel):
    device_id: str
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=500)
    stack: str
    build_cmd: list[str] | None = None
    test_cmd: list[str] | None = None
    deploy_cmd: list[str] | None = None
    git_url: str | None = None


class FileIn(BaseModel):
    path: str = Field(max_length=500)
    content: str = Field(max_length=2_000_000)


class RunIn(BaseModel):
    which: str


class TermIn(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=30)
    timeout: int = Field(default=300, ge=1, le=3600)


class RollbackIn(BaseModel):
    backup_id: str


class TaskIn(BaseModel):
    device_id: str
    kind: str
    instruction: str = Field(default="", max_length=4000)
    project_id: str | None = None
    mode: str = "auto"
    tools: list[str] = []
    actions: list[dict] = []
    allow_bash: bool = False


class TApproveIn(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    confirm_high_risk: bool = False
    confirm_text: str | None = Field(default=None, max_length=200)


class SrvIn(BaseModel):
    tool_id: str
    params: dict = {}


class FsWriteIn(BaseModel):
    path: str = Field(max_length=500)
    content_b64: str = Field(max_length=8_000_000)


def build_router(core: Core, dev: Dev, tasks: Tasks) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    def run(f):
        try:
            return f()
        except TargetError as e:
            raise HTTPException(e.status, str(e))
        except NetworkPolicyError as e:
            raise HTTPException(422, str(e))

    # ---- overview
    @r.get("/features")
    def features():
        return matrix()

    @r.post("/emergency-stop")
    def stop():
        return core.emergency_stop()

    @r.get("/local-device")
    def local():
        return core.ensure_local_device()

    # ---- connections
    @r.get("/connections/methods")
    def methods():
        return C.METHODS

    @r.get("/connections/local")
    def local_caps():
        return C.local_capabilities()

    @r.post("/connections/probe")
    def probe(b: ProbeIn):
        d = run(lambda: C.probe(b.address, b.allow_public))
        core.db.audit("technician", "connection.probe", None, address=b.address, open=[o["method"] for o in d["open"]])
        return d

    @r.get("/connections/tailscale")
    def tailscale():
        return C.tailscale_peers()

    @r.post("/connections/rustdesk")
    def rustdesk(b: RustDeskIn):
        try:
            res = C.rustdesk_launch(b.remote_id)
        except ValueError as e:
            raise HTTPException(422, str(e))
        core.db.audit("technician", "rustdesk.launch", None, remote_id=b.remote_id, launched=res["launched"])
        return res

    @r.post("/connections/git")
    def git(b: GitIn):
        return C.git_check(b.url)

    @r.get("/devices/{did}/connection-test")
    def conn_test(did: str):
        return C.agent_test(core, did)

    @r.get("/devices/{did}/info")
    def device_info(did: str):
        """Live facts from the machine itself (OS, host name, tools, what the owner allowed) + the session state."""
        d = core.device(did)
        info = run(lambda: core.target(did).info())
        return {"device": d, "info": info, "sessions": core.sessions(did)[:3]}

    # ---- projects
    @r.get("/stacks")
    def stacks():
        return {k: {"build": v[0], "test": v[1]} for k, v in STACKS.items()}

    @r.post("/projects", status_code=201)
    def mk_project(b: ProjectIn):
        return dev.create(b.device_id, b.name, b.root, b.stack, b.build_cmd, b.test_cmd, b.deploy_cmd, b.git_url)

    @r.get("/projects")
    def projects(device_id: str | None = None):
        return dev.projects(device_id)

    @r.get("/projects/{pid}")
    def project(pid: str):
        return dev.project(pid)

    @r.get("/projects/{pid}/tree")
    def tree(pid: str, path: str = ""):
        return dev.tree(pid, path)

    @r.get("/projects/{pid}/file")
    def read(pid: str, path: str):
        return dev.read(pid, path)

    @r.put("/projects/{pid}/file")
    def write(pid: str, b: FileIn):
        return dev.write(pid, b.path, b.content)

    @r.get("/projects/{pid}/git/{what}")
    def git_(pid: str, what: str):
        return dev.git(pid, what)

    @r.post("/projects/{pid}/run")
    def run_(pid: str, b: RunIn):
        return dev.command(pid, b.which)

    @r.post("/projects/{pid}/terminal")
    def term(pid: str, b: TermIn):
        return dev.terminal(pid, b.argv, b.timeout)

    @r.get("/projects/{pid}/env")
    def env(pid: str):
        return dev.environment(pid)

    @r.post("/projects/{pid}/backup", status_code=201)
    def backup(pid: str):
        return dev.backup(pid)

    @r.get("/projects/{pid}/backups")
    def backups(pid: str):
        return dev.backups(pid)

    @r.post("/projects/{pid}/rollback")
    def rollback(pid: str, b: RollbackIn):
        return dev.rollback(pid, b.backup_id)

    # ---- tasks
    @r.get("/task-kinds")
    def kinds():
        return {"kinds": KINDS, "claude_central_available": tasks.central_available()}

    @r.post("/tasks", status_code=201)
    def mk_task(b: TaskIn):
        return tasks.create(b.device_id, b.kind, b.instruction, b.project_id, b.mode, b.tools, b.actions, b.allow_bash)

    @r.get("/tasks")
    def list_tasks(device_id: str | None = None, kind: str | None = None):
        return tasks.list(device_id, kind)

    @r.get("/tasks/{tid}")
    def task(tid: str, since: int = 0):
        t = tasks.get(tid)
        t["event_count"] = len(t["events"]); t["events"] = t["events"][since:]
        return t

    @r.post("/tasks/{tid}/steps/{sid}/approve")
    def t_approve(tid: str, sid: str, b: TApproveIn):
        return tasks.approve(tid, sid, b.approver, b.confirm_high_risk, b.confirm_text)

    @r.post("/tasks/{tid}/approve-low-risk")
    def t_low(tid: str, b: TApproveIn):
        return tasks.approve_low_risk(tid, b.approver)

    @r.post("/tasks/{tid}/steps/{sid}/reject")
    def t_reject(tid: str, sid: str):
        return tasks.reject(tid, sid)

    @r.post("/tasks/{tid}/execute")
    def t_exec(tid: str):
        return tasks.execute(tid)

    @r.post("/tasks/{tid}/cancel")
    def t_cancel(tid: str):
        return tasks.cancel(tid)

    @r.post("/tasks/{tid}/rollback")
    def t_rollback(tid: str):
        return tasks.rollback(tid)

    @r.get("/tasks/{tid}/report", response_class=PlainTextResponse)
    def t_report(tid: str):
        t = tasks.get(tid)
        return t["report"] or tasks.report(t)

    # ---- servers
    @r.get("/server-tools")
    def server_tools():
        return [{"id": t.id, "title": t.title, "risk": t.risk.value, "params": t.params, "production": t.production} for t in SERVER_TOOLS.values()]

    @r.post("/devices/{did}/server-read")
    def server_read(did: str, b: SrvIn):
        st = server_tool(b.tool_id)
        if st.risk != Risk.READ_ONLY:
            raise HTTPException(403, "only read-only actions run immediately; create a 'server' task to get approvals")
        argv = st.build(b.params)
        res = run(lambda: run_sync(core.target(did), argv, None, st.timeout_s))
        core.db.audit("technician", "server.read", did, tool=st.id, argv=redact_argv(argv), exit=res["exit"])
        return res

    # ---- file manager (confined to the roots the owner authorized)
    @r.get("/devices/{did}/fs/list")
    def fs_list(did: str, path: str | None = None):
        t = core.target(did)
        p = path or (t.info().get("roots") or ["/"])[0]
        return {"path": p, "entries": run(lambda: t.fs_list(p))}

    @r.get("/devices/{did}/fs/read")
    def fs_read(did: str, path: str):
        t = core.target(did)
        data = run(lambda: t.fs_read(path))
        core.db.audit("technician", "fs.read", did, path=path, bytes=len(data))
        return {"content_b64": b64e(data), "size": len(data)}

    @r.put("/devices/{did}/fs/write")
    def fs_write(did: str, b: FsWriteIn):
        t = core.target(did, "repair")
        run(lambda: t.fs_write(b.path, b64d(b.content_b64)))
        core.db.audit("technician", "fs.write", did, path=b.path)
        return {"ok": True}

    return r
