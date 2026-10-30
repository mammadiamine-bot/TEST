import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from app.targetcore import Grants
from tests.conftest import onboard
from tests.test_agent import cit_agent
from tests.test_tasks import XorProtector, install_fake, HERE


@pytest.fixture
def stack(tmp_path, monkeypatch):
    install_fake(tmp_path / "bin", "claude", HERE / "fake_claude.py")
    monkeypatch.setenv("PATH", f"{tmp_path/'bin'}{os.pathsep}{os.environ['PATH']}")
    proj = tmp_path / "proj"; proj.mkdir(); (proj / "app.py").write_text("print(1)\n")
    s = Settings(data_dir=tmp_path / "d", api_token="t", local_roots=[str(tmp_path)])
    core = Core(s, Db(":memory:"), protector=XorProtector(), cloud_probe=lambda: True)
    c = TestClient(create_app(s, core), headers={"Authorization": "Bearer t"})
    return c, core, proj


def test_all_new_routes_require_token(stack):
    c, *_ = stack
    anon = TestClient(c.app)
    for m, p in [("get", "/features"), ("post", "/emergency-stop"), ("get", "/projects"), ("post", "/tasks"), ("get", "/connections/methods"),
                 ("post", "/connections/probe"), ("get", "/devices/x/fs/list"), ("post", "/projects/x/terminal"), ("get", "/server-tools")]:
        assert getattr(anon, m)(p).status_code == 401, p


def test_features_and_connection_endpoints(stack):
    c, *_ = stack
    assert {f["status"] for f in c.get("/features").json()} <= {"tested", "untested", "mock", "planned", "blocked"}
    assert len(c.get("/connections/methods").json()) == 10
    assert c.post("/connections/probe", json={"address": "8.8.8.8"}).status_code == 422
    assert c.post("/connections/rustdesk", json={"remote_id": "1; calc"}).status_code == 422
    assert c.post("/connections/git", json={"url": "--evil"}).json()["ok"] is False


def test_local_device_projects_files_terminal_over_http(stack):
    c, core, proj = stack
    devs = c.get("/devices").json()
    loc = next(d for d in devs if d["transport"] == "local")
    assert loc["kind"] == "control" and c.get("/devices").json().count(loc) == 1
    p = c.post("/projects", json={"device_id": loc["id"], "name": "demo", "root": str(proj), "stack": "python", "test_cmd": [sys.executable, "-c", "print('ok')"]}).json()
    assert [e["name"] for e in c.get(f"/projects/{p['id']}/tree").json()] == ["app.py"]
    assert c.get(f"/projects/{p['id']}/file", params={"path": "app.py"}).json()["content"] == "print(1)\n"
    assert c.get(f"/projects/{p['id']}/file", params={"path": "../x"}).status_code == 422
    assert c.put(f"/projects/{p['id']}/file", json={"path": "app.py", "content": "print(2)\n"}).status_code == 200
    assert (proj / "app.py").read_text() == "print(2)\n" and list((core.s.data_dir / "backups/edits").rglob("*.bak"))  # previous version kept
    assert c.post(f"/projects/{p['id']}/run", json={"which": "test"}).json()["output"].strip() == "ok"
    t = c.post(f"/projects/{p['id']}/terminal", json={"argv": [sys.executable, "-c", "print(7)", "--password", "hunter2"]}).json()
    assert t["output"].strip() == "7"
    assert "hunter2" not in str(c.get("/audit").json())
    bid = c.post(f"/projects/{p['id']}/backup").json()["id"]
    c.put(f"/projects/{p['id']}/file", json={"path": "app.py", "content": "broken"})
    assert c.post(f"/projects/{p['id']}/rollback", json={"backup_id": bid}).json()["restored"] == 1
    assert (proj / "app.py").read_text() == "print(2)\n"
    assert c.post(f"/projects/{p['id']}/rollback", json={"backup_id": "../../etc"}).status_code == 422
    fl = c.get(f"/devices/{loc['id']}/fs/list", params={"path": str(proj)}).json()
    assert fl["entries"][0]["name"] == "app.py"
    assert c.get(f"/devices/{loc['id']}/fs/read", params={"path": "/etc/passwd"}).status_code == 403


def test_task_api_roundtrip_and_report(stack):
    c, core, proj = stack
    loc = next(d for d in c.get("/devices").json() if d["transport"] == "local")
    p = c.post("/projects", json={"device_id": loc["id"], "name": "demo", "root": str(proj), "stack": "other"}).json()
    t = c.post("/tasks", json={"device_id": loc["id"], "kind": "dev", "instruction": "FAKE:replace print(1)=>print(3) in app.py", "project_id": p["id"], "mode": "central"}).json()
    c.post(f"/tasks/{t['id']}/approve-low-risk", json={"approver": "T"})
    c.post(f"/tasks/{t['id']}/execute")
    import time
    for _ in range(100):
        d = c.get(f"/tasks/{t['id']}").json()
        if d["status"] not in ("running", "approved"):
            break
        time.sleep(0.1)
    assert d["status"] == "completed_with_issues"  # no test command => honest "not fully verified"
    assert (proj / "app.py").read_text() == "print(3)\n"
    assert "NOT verified" in c.get(f"/tasks/{t['id']}/report").text
    assert c.get(f"/tasks/{t['id']}", params={"since": d["event_count"]}).json()["events"] == []
    assert c.post("/tasks", json={"device_id": loc["id"], "kind": "nonsense"}).status_code == 422


def test_server_read_is_readonly_only(stack):
    c, *_ = stack
    loc = next(d for d in c.get("/devices").json() if d["transport"] == "local")
    assert c.post(f"/devices/{loc['id']}/server-read", json={"tool_id": "svc_restart", "params": {"unit": "nginx"}}).status_code == 403
    assert c.post(f"/devices/{loc['id']}/server-read", json={"tool_id": "srv_uptime"}).json()["exit"] == 0


def test_emergency_stop_endpoint_and_device_create_fields(stack):
    c, core, proj = stack
    did, sid = onboard(c)
    assert c.post("/emergency-stop").json()["sessions_revoked"] >= 1
    assert c.post(f"/devices/{did}/diagnose", json={}).status_code == 403
    r = c.post("/devices", json={"name": "v", "address": "203.0.113.5", "transport": "ssh", "owner_name": "me", "owner_contact": "me@x.io",
                                 "authorization_note": "my own VPS, key-only access", "kind": "server", "allow_public": True})
    assert r.status_code == 201 and r.json()["kind"] == "server"
