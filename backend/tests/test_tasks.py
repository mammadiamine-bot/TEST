import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.dev import Dev
from app.service import Core, CoreError
from app.targetcore import Grants
from app.tasks import INSTALLERS, Tasks
from app.transports.agent import AgentClient
from tests.test_agent import cit_agent

HERE = Path(__file__).parent


class XorProtector:  # TEST protector standing in for Windows DPAPI
    def protect(self, d): return bytes(b ^ 0x5A for b in d)
    unprotect = protect


def install_fake(bindir: Path, name: str, src: Path):
    bindir.mkdir(exist_ok=True)
    w = bindir / name
    w.write_text(f"#!/bin/sh\nexec {sys.executable} {src} \"$@\"\n"); w.chmod(w.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    install_fake(bindir, "claude", HERE / "fake_claude.py")
    install_fake(bindir, "ssh", HERE / "fake_ssh.py")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    proj = tmp_path / "client_proj"; proj.mkdir()
    (proj / "app.py").write_text("print('hello')\n# TODO fix\n")
    (proj / "old.txt").write_text("remove me")
    (proj / "check.py").write_text("import sys;sys.exit(0 if 'DONE' in open('app.py').read() else 1)\n")
    core = Core(Settings(data_dir=tmp_path / "data", api_token="t", ssh_bin=str(bindir / "ssh")), Db(":memory:"), protector=XorProtector(), cloud_probe=lambda: True)
    dev = Dev(core); tasks = Tasks(core, dev)
    return type("E", (), dict(core=core, dev=dev, tasks=tasks, proj=proj, bindir=bindir, tmp=tmp_path, log=tmp_path / "claude.log"))


def agent_device(env, grants=Grants(terminal=True, write=True, claude=True), name="Client PC"):
    ag = cit_agent.Agent([env.proj], grants, 30, say=lambda *_: None)
    srv, fp = cit_agent.serve(ag, "127.0.0.1", 0)
    d = env.core.create_device(name, f"127.0.0.1:{srv.server_address[1]}", "agent", "Owner", "o@x.io", "Owner agreed in writing")
    assert "pairing_code" not in d  # the code lives on the client's screen only
    env.core.pair(d["id"], ag.code, fp)
    return d["id"], ag, srv


def project(env, did, **kw):
    kw.setdefault("test_cmd", [sys.executable, "check.py"]); kw.setdefault("build_cmd", None)
    return env.dev.create(did, "demo", str(env.proj), "python", **kw)


def approve_all(env, tid, confirm_high=True, text=None):
    for s in env.tasks.get(tid)["steps"]:
        if s["status"] == "proposed":
            env.tasks.approve(tid, s["id"], "Tech", confirm_high, text)


# ---------------------------------------------------------------- agent integration into the original engine
def test_agent_device_end_to_end_consent_and_session(env):
    did, ag, srv = agent_device(env)
    d = env.core.device(did)
    assert d["status"] == "paired" and d["live"]
    s = env.core.sessions(did)[0]
    assert s["status"] == "active" and s["consenting_person"].startswith("client")
    env.core.end_session(s["id"], "tech")
    assert ag.ended.is_set()  # revoking on the controller really disconnects the agent
    with pytest.raises(CoreError):
        env.core.target(did)


def test_agent_pair_requires_fingerprint_and_right_code(env):
    ag = cit_agent.Agent([env.proj], Grants(), 30, say=lambda *_: None)
    srv, fp = cit_agent.serve(ag, "127.0.0.1", 0)
    d = env.core.create_device("C", f"127.0.0.1:{srv.server_address[1]}", "agent", "o", "o@x.io", "authorized by owner")
    with pytest.raises(CoreError, match="fingerprint"):
        env.core.pair(d["id"], ag.code, "")
    with pytest.raises(CoreError, match="identity mismatch"):
        env.core.pair(d["id"], ag.code, "ab" * 32)
    with pytest.raises(CoreError):
        env.core.pair(d["id"], "00000000", fp)
    assert env.core.device(d["id"])["status"] == "pending_pairing"


def test_public_agent_address_rejected(env):
    with pytest.raises(CoreError):
        env.core.create_device("x", "8.8.8.8:8766", "agent", "o", "o@x.io", "authorized by owner")


def test_public_ssh_server_needs_explicit_optin(env):
    with pytest.raises(CoreError):
        env.core.create_device("vps", "203.0.113.10", "ssh", "me", "me@x.io", "my own VPS, key-only access", kind="server")
    with pytest.raises(CoreError, match="only allowed for SSH servers"):
        env.core.create_device("vps", "203.0.113.10", "winrm", "me", "me@x.io", "my own VPS, key-only access", allow_public=True)
    d = env.core.create_device("vps", "203.0.113.10", "ssh", "me", "me@x.io", "my own VPS, key-only access", kind="server", allow_public=True)
    assert d["kind"] == "server"
    assert any(a["action"] == "device.public_server_ack" for a in env.core.audit_log())


def test_existing_windows_diagnostics_run_through_agent(env):
    calls = []
    ag = cit_agent.Agent([env.proj], Grants(), 30, say=lambda *_: None, ps_runner=lambda sc, t: (calls.append(sc) or (True, '{"cpu_percent":5,"ram_total_gb":8,"ram_used_gb":2,"ram_percent":25}', "")))
    srv, fp = cit_agent.serve(ag, "127.0.0.1", 0)
    d = env.core.create_device("W", f"127.0.0.1:{srv.server_address[1]}", "agent", "o", "o@x.io", "authorized by owner")
    env.core.pair(d["id"], ag.code, fp)
    run = env.core.diagnose(d["id"], ["cpu_ram"])
    assert run["errors"] == {} and run["data"]["cpu_ram"]["cpu_percent"] == 5 and calls


# ---------------------------------------------------------------- central Claude
def test_central_flow_with_approvals_verify_and_rollback(env):
    did, ag, srv = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:replace # TODO fix=>DONE in app.py", p["id"], mode="central")
    assert t["mode"] == "central" and [s["type"] for s in t["steps"]] == ["detect", "pull", "claude", "review", "push", "verify"]
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "awaiting_approval" and (env.proj / "app.py").read_text().endswith("TODO fix\n")  # nothing changed yet
    env.tasks.approve(t["id"], "s3", "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "awaiting_approval" and t["changes"][0]["path"] == "app.py" and "+DONE" in t["changes"][0]["diff"]
    assert (env.proj / "app.py").read_text().endswith("TODO fix\n")  # still untouched until push is approved
    env.tasks.approve(t["id"], "s5", "Tech"); env.tasks.approve(t["id"], "s6", "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "completed", [(s["title"], s["status"], s["result"]) for s in t["steps"]]
    assert "DONE" in (env.proj / "app.py").read_text()
    assert t["steps"][-1]["status"] == "verified" and "# Task report" in t["report"] and "Approved by" in t["report"]
    assert any("tool Edit" in e["msg"] for e in t["events"])  # live progress from Claude's stream
    log = [json.loads(l) for l in env.log.read_text().splitlines()]
    assert "bypassPermissions" not in log[0]["argv"] and "Bash" not in " ".join(log[0]["argv"]) and "acceptEdits" in log[0]["argv"]
    env.tasks.rollback(t["id"])
    assert (env.proj / "app.py").read_text().endswith("TODO fix\n")


def test_deletions_make_push_high_risk(env):
    did, *_ = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:delete old.txt", p["id"], mode="central")
    env.tasks.approve(t["id"], "s3", "Tech")
    t = env.tasks.execute(t["id"], background=False)
    push = next(s for s in t["steps"] if s["type"] == "push")
    assert push["risk"] == "high" and "DELETIONS" in push["title"]
    with pytest.raises(CoreError) as e:
        env.tasks.approve(t["id"], push["id"], "Tech")
    assert e.value.status == 428
    env.tasks.approve(t["id"], push["id"], "Tech", True)
    env.tasks.approve(t["id"], "s6", "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert not (env.proj / "old.txt").exists()
    env.tasks.rollback(t["id"])
    assert (env.proj / "old.txt").read_text() == "remove me"


def test_failed_verification_is_not_success(env):
    did, *_ = agent_device(env)
    p = project(env, did)  # check.py fails unless app.py contains DONE
    t = env.tasks.create(did, "dev", "FAKE:replace hello=>hi in app.py", p["id"], mode="central")
    env.tasks.approve_low_risk(t["id"], "Tech")
    t = env.tasks.execute(t["id"], background=False)
    t = env.tasks.execute(t["id"], background=False) if t["status"] == "awaiting_approval" else t
    assert t["status"] == "failed" and t["steps"][-1]["status"] == "failed"


def test_no_test_command_reports_unverified(env):
    did, *_ = agent_device(env)
    p = env.dev.create(did, "demo", str(env.proj), "other")  # stack 'other' has no default test command
    assert p["test_cmd"] is None
    t = env.tasks.create(did, "dev", "FAKE:replace hello=>hi in app.py", p["id"], mode="central")
    env.tasks.approve_low_risk(t["id"], "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "completed_with_issues" and "NOT verified" in t["steps"][-1]["result"]["detail"]


def test_claude_failure_stops_task(env):
    did, *_ = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:fail", p["id"], mode="central")
    env.tasks.approve(t["id"], "s3", "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "failed" and any("Invalid API key" in e["msg"] for e in t["events"])
    assert (env.proj / "app.py").read_text().startswith("print('hello')")


# ---------------------------------------------------------------- remote Claude
def test_remote_flow_runs_on_target_with_backup_and_confirm(env):
    did, ag, srv = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:replace # TODO fix=>DONE in app.py", p["id"])
    assert t["mode"] == "remote" and "owner allowed" in t["mode_reason"]
    step = next(s for s in t["steps"] if s["type"] == "claude_remote")
    assert step["risk"] == "high"
    with pytest.raises(CoreError) as e:
        env.tasks.approve(t["id"], step["id"], "Tech")
    assert e.value.status == 428
    approve_all(env, t["id"])
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "completed" and "DONE" in (env.proj / "app.py").read_text()
    assert env.dev.backups(p["id"]) and any("backed up" in e["msg"] for e in t["events"])
    b = env.dev.backups(p["id"])[0]["id"]
    env.dev.rollback(p["id"], b)
    assert (env.proj / "app.py").read_text().endswith("TODO fix\n")
    log = [json.loads(l) for l in env.log.read_text().splitlines()]
    assert log[-1]["cwd"] == str(env.proj.resolve())


def test_remote_blocked_without_grant_or_tool(env):
    did, *_ = agent_device(env, Grants(terminal=True, write=True))
    p = project(env, did)
    t = env.tasks.create(did, "dev", "x", p["id"], mode="remote")
    assert t["status"] == "blocked" and "--allow-claude" in t["mode_reason"] and "OWN account" in t["mode_reason"]
    with pytest.raises(CoreError):
        env.tasks.execute(t["id"])
    # auto falls back to central when this computer has Claude
    t2 = env.tasks.create(did, "dev", "x", p["id"])
    assert t2["mode"] == "central"


def test_central_blocked_when_claude_missing_locally(env, monkeypatch):
    did, *_ = agent_device(env, Grants(terminal=True, write=True))
    p = project(env, did)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    t = env.tasks.create(did, "dev", "x", p["id"])
    assert t["status"] == "blocked" and "No Claude Code available" in t["mode_reason"]


def test_cross_device_project_isolation(env):
    a, *_ = agent_device(env, name="Client A")
    b, *_ = agent_device(env, name="Client B")
    pa = project(env, a)
    with pytest.raises(CoreError) as e:
        env.tasks.create(b, "dev", "x", pa["id"], mode="central")
    assert e.value.status == 403 and "different device" in str(e.value)


def test_cancel_running_remote_job_and_emergency_stop(env):
    did, ag, srv = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:sleep", p["id"], mode="remote")
    approve_all(env, t["id"])
    env.tasks.execute(t["id"])
    for _ in range(100):
        if any("working" in e["msg"] for e in env.tasks.get(t["id"])["events"]):
            break
        time.sleep(0.1)
    res = env.core.emergency_stop()
    t = env.tasks.wait(t["id"], 20)
    assert t["status"] in ("cancelled", "failed") and res["sessions_revoked"] >= 1
    assert ag.ended.is_set() and all(s["status"] != "active" for s in env.core.sessions(did))
    assert any(a["action"] == "emergency.stop" for a in env.core.audit_log())


def test_cancel_task_kills_job(env):
    did, ag, srv = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:sleep", p["id"], mode="remote")
    approve_all(env, t["id"])
    env.tasks.execute(t["id"])
    time.sleep(1.0)
    env.tasks.cancel(t["id"])
    t = env.tasks.wait(t["id"], 20)
    assert t["status"] == "cancelled"
    assert ag.jobs.get(t["remote_job"]).cancelled


def test_secrets_never_reach_task_log(env):
    did, *_ = agent_device(env)
    p = project(env, did)
    t = env.tasks.create(did, "dev", "FAKE:replace hello=>hi in app.py password=hunter2 sk-ant-abcdefghijklmnop", p["id"], mode="central")
    env.tasks.approve_low_risk(t["id"], "Tech")
    env.tasks.execute(t["id"], background=False)
    assert "hunter2" not in json.dumps(env.tasks.get(t["id"])["events"]) and "sk-ant" not in json.dumps(env.tasks.get(t["id"])["events"])


def test_one_task_at_a_time_per_device(env):
    did, *_ = agent_device(env)
    p = project(env, did)
    t1 = env.tasks.create(did, "dev", "FAKE:sleep", p["id"], mode="remote"); approve_all(env, t1["id"])
    t2 = env.tasks.create(did, "dev", "x", p["id"], mode="remote"); approve_all(env, t2["id"])
    env.tasks.execute(t1["id"])
    with pytest.raises(CoreError, match="another task"):
        env.tasks.execute(t2["id"])
    env.tasks.cancel(t1["id"]); env.tasks.wait(t1["id"], 20)


# ---------------------------------------------------------------- install tools
def test_install_tools_plan_approval_and_verification(env, monkeypatch):
    did, *_ = agent_device(env)
    monkeypatch.setenv("PATH", f"/usr/bin:/bin")
    t = env.tasks.create(did, "install_tools", "", tools_wanted=["claude"])
    st = next(s for s in t["steps"] if s["type"] == "install_tool")
    assert st["risk"] == "high" and st["params"]["argv"][:2] == ["npm", "install"] and st["status"] == "proposed"
    # swap the real installer for a harmless one that drops a `claude` binary; the task must re-detect it
    st["params"]["argv"] = [sys.executable, "-c", f"import shutil;shutil.copy('{env.bindir/'claude'}','{env.proj/'claude'}')"]
    monkeypatch.setenv("PATH", f"{env.proj}:/usr/bin:/bin")
    with pytest.raises(CoreError):
        env.tasks.approve(t["id"], st["id"], "Tech")  # high risk
    assert not (env.proj / "claude").exists()
    t["steps"] = [s for s in t["steps"]]; env.tasks._save(t)
    env.tasks.approve(t["id"], st["id"], "Tech", True)
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "completed" and "now detected" in t["steps"][-1]["result"]["detail"]


def test_install_rejects_unknown_tool(env):
    did, *_ = agent_device(env)
    with pytest.raises(CoreError):
        env.tasks.create(did, "install_tools", "", tools_wanted=["rm -rf /"])
    assert all(isinstance(a, list) for a in INSTALLERS.values())


# ---------------------------------------------------------------- servers
def ssh_server(env):
    d = env.core.create_device("VPS", "10.0.0.5", "ssh", "me", "me@x.io", "my own server, key-only access", kind="server")
    import base64
    line = f"10.0.0.5 ssh-ed25519 {base64.b64encode(b'k').decode()}"
    from app.service import ssh_fingerprint
    env.core.pair(d["id"], d["pairing_code"], ssh_fingerprint(line), line)
    s = env.core.request_session(d["id"], "repair", 30)
    env.core.consent(s["id"], s["consent_code"], "me")
    env.core.set_credentials(d["id"], {"user": "ops", "key_path": "/k"})
    return d["id"]


def test_server_task_readonly_runs_and_production_change_needs_typed_name(env):
    did = ssh_server(env)
    t = env.tasks.create(did, "server", "", actions=[{"tool_id": "srv_uptime"}, {"tool_id": "svc_restart", "params": {"unit": "nginx"}}])
    assert [s["risk"] for s in t["steps"]] == ["read_only", "high"]
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "awaiting_approval" and t["steps"][0]["status"] == "verified" and any("load average" in e["msg"] for e in t["events"])
    with pytest.raises(CoreError) as e:
        env.tasks.approve(t["id"], "s2", "Tech", True)
    assert e.value.status == 428 and "type the exact name" in str(e.value)
    with pytest.raises(CoreError):
        env.tasks.approve(t["id"], "s2", "Tech", True, "wrong")
    env.tasks.approve(t["id"], "s2", "Tech", True, "VPS")  # approval recorded; we do not execute a restart in tests
    assert env.tasks.get(t["id"])["steps"][1]["status"] == "approved"


def test_server_task_rejects_non_allowlisted_actions(env):
    did = ssh_server(env)
    for bad in ({"tool_id": "rm_rf"}, {"tool_id": "svc_restart", "params": {"unit": "x; reboot"}}):
        with pytest.raises(Exception):
            env.tasks.create(did, "server", "", actions=[bad])


# ---------------------------------------------------------------- diagnose + local + deploy
def test_diagnose_task_on_mock_device(env):
    d = env.core.create_device("M", "mock", "mock", "o", "o@x.io", "owner agreed in writing")
    env.core.pair(d["id"], d["pairing_code"], "")
    s = env.core.request_session(d["id"], "repair", 30); env.core.consent(s["id"], s["consent_code"], "o")
    with pytest.raises(Exception):  # mock has no Target (no project/terminal access) — honest refusal
        env.tasks.create(d["id"], "diagnose")


def test_local_device_dev_task_and_deploy_confirmation(env):
    env.core.s.local_roots = [str(env.proj.parent)]
    loc = env.core.ensure_local_device()
    p = env.dev.create(loc["id"], "local", str(env.proj), "python", test_cmd=[sys.executable, "check.py"], deploy_cmd=[sys.executable, "-c", "print('deployed')"])
    t = env.tasks.create(loc["id"], "dev", "FAKE:replace # TODO fix=>DONE in app.py", p["id"], mode="central")
    env.tasks.approve_low_risk(t["id"], "Tech")
    t = env.tasks.execute(t["id"], background=False)
    assert t["status"] == "completed", [(s["title"], s["status"], s["result"]) for s in t["steps"]]
    d = env.tasks.create(loc["id"], "deploy", "", p["id"])
    dep = next(s for s in d["steps"] if s["type"] == "deploy")
    assert dep["production"] and dep["risk"] == "high"
    with pytest.raises(CoreError) as e:
        env.tasks.approve(d["id"], dep["id"], "Tech", True)
    assert e.value.status == 428
    env.tasks.approve_low_risk(d["id"], "Tech"); env.tasks.approve(d["id"], dep["id"], "Tech", True, "local")
    d = env.tasks.execute(d["id"], background=False)
    assert d["status"] == "completed" and any("deployed" in e["msg"] for e in d["events"])
