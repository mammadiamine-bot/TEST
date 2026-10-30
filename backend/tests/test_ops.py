import json
import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core, CoreError
from tests.test_tasks import XorProtector


class FakeWorkers:
    """Speaks the Worker's exec/job protocol. 'FAIL' in the command -> exit 1, 'SLOW' -> never finishes until cancelled."""
    def __init__(self):
        self.n, self.jobs, self.unreachable, self.calls = 0, {}, 0, []

    def __call__(self, wid, method, path, body=None, actor="x"):
        self.calls.append((wid, method, path))
        if path == "/clients":
            raise CoreError("exists", 409)
        if path.endswith("/exec"):
            if self.unreachable > 0:
                self.unreachable -= 1
                raise CoreError("worker unreachable: timed out", 502)
            self.n += 1
            self.jobs[f"j{self.n}"] = {"argv": body["argv"], "cancelled": False}
            return {"job": f"j{self.n}"}
        if path.endswith("/cancel"):
            self.jobs[path.split("/")[-2]]["cancelled"] = True
            return {"cancelled": True}
        j = self.jobs[path.split("?")[0].split("/")[-1]]
        cmd = j["argv"][-1]
        if j["cancelled"]:
            return {"output": "", "next": 0, "done": True, "exit": 1, "cancelled": True}
        if "SLOW" in cmd:
            return {"output": "", "next": 0, "done": False}
        if "ConvertTo-Json" in cmd:
            return {"output": json.dumps({"host": "PC1", "ram_gb": 16}), "next": 1, "done": True, "exit": 0}
        return {"output": f"ran:{cmd[:30]}", "next": 1, "done": True, "exit": 1 if "FAIL" in cmd else 0}


@pytest.fixture
def w(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    fake = FakeWorkers()
    app.state.ops.call, app.state.ops.retry_delay = fake, 0.0
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]
    wk = {}
    for k, org in (("a", a), ("a2", a), ("b", b)):
        wid = f"wst_{k}0000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)", (wid, f"{k}-pc", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org))
        wk[k] = wid

    def mint(role, orgs):
        r = owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()
        return r, TestClient(app, headers={"Authorization": f"Bearer {r['token']}"})
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, wk=wk, fake=fake, ops=app.state.ops, mint=mint))


def done(w, oid, jid, client=None, timeout=10):
    c = client or w.owner
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/orgs/{oid}/jobs/{jid}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_read_template_runs_without_approval_and_keeps_device_history(w):
    j = w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "services.list", "targets": [w.wk["a"], w.wk["a2"]]}).json()
    assert j["approval"] == "not_required"
    j = done(w, w.a, j["id"])
    assert j["status"] == "completed" and {r["status"] for r in j["runs"]} == {"ok"}
    argv = w.fake.jobs["j1"]["argv"]
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"] and "Get-Service" in argv[-1]
    hist = w.owner.get(f"/workers/{w.wk['a']}/runs").json()
    assert len(hist) == 1 and hist[0]["status"] == "ok" and hist[0]["title"].startswith("Windows services")


def test_freeform_commands_need_a_second_person_and_cannot_be_self_approved(w):
    _, tech = w.mint("technician", [w.a])
    adm_info, adm = w.mint("administrator", [w.a])
    adm2_info, adm2 = w.mint("administrator", [w.a])
    j = tech.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "ipconfig /flushdns", "targets": [w.wk["a"]]}).json()
    assert j["status"] == "pending_approval" and j["risk"] == "modify" and w.fake.n == 0     # nothing ran yet
    assert tech.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 403    # technicians cannot approve
    mine = adm.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "echo hi", "targets": [w.wk["a"]]}).json()
    assert adm.post(f"/orgs/{w.a}/jobs/{mine['id']}/approve", json={}).status_code == 403  # four-eyes: not your own job
    assert adm2.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 200
    assert done(w, w.a, j["id"])["status"] == "completed"
    assert w.owner.post(f"/orgs/{w.a}/jobs/{mine['id']}/approve", json={}).status_code == 200   # the owner may approve
    log = [x["action"] for x in w.db.all("SELECT action FROM audit")]
    assert "job.created" in log and "job.approved" in log and w.db.verify_audit_chain()


def test_disruptive_jobs_need_typed_confirmation_and_cannot_be_scheduled(w):
    j = w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "system.reboot", "targets": [w.wk["a"]]}).json()
    assert j["risk"] == "disruptive" and j["status"] == "pending_approval"
    assert w.owner.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 422
    assert w.fake.n == 0
    assert w.owner.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={"confirm": "I APPROVE"}).status_code == 200
    done(w, w.a, j["id"])
    assert "shutdown" in w.fake.jobs["j1"]["argv"][-1]
    r = w.owner.post(f"/orgs/{w.a}/schedules", json={"title": "nightly reboot", "targets": [w.wk["a"]], "every_minutes": 60, "template": "system.reboot"})
    assert r.status_code == 422
    rej = w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "system.shutdown", "targets": [w.wk["a"]]}).json()
    assert w.owner.post(f"/orgs/{w.a}/jobs/{rej['id']}/reject").json()["status"] == "cancelled"


def test_dispatch_never_crosses_clients_and_templates_reject_injection(w):
    assert w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "services.list", "targets": [w.wk["b"]]}).status_code == 422
    _, ta = w.mint("technician", [w.a])
    jb = w.owner.post(f"/orgs/{w.b}/jobs", json={"template": "services.list", "targets": [w.wk["b"]]}).json()
    assert ta.post(f"/orgs/{w.b}/jobs", json={"template": "services.list", "targets": [w.wk["b"]]}).status_code == 403
    assert ta.get(f"/orgs/{w.b}/jobs/{jb['id']}").status_code == 403 and ta.get(f"/workers/{w.wk['b']}/runs").status_code == 403
    assert ta.get(f"/orgs/{w.a}/jobs/{jb['id']}").status_code in (403, 404)                 # another client's job id under my org
    for bad in ("x'; calc; '", "a b", "x" * 80):
        assert w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "service.restart", "params": {"name": bad}, "targets": [w.wk["a"]]}).status_code == 422
    assert w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "eventlog.recent", "params": {"log": "Security"}, "targets": [w.wk["a"]]}).status_code == 422
    assert w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "service.restart", "params": {"name": "Spooler"}, "targets": [w.wk["a"]]}).json()["status"] == "pending_approval"


def test_retry_on_unreachable_but_not_on_command_failure(w):
    w.fake.unreachable = 1
    j = w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "services.list", "targets": [w.wk["a"]], "retries": 2}).json()
    j = done(w, w.a, j["id"])
    assert j["status"] == "completed" and j["runs"][0]["attempt"] == 2
    f = w.owner.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "FAIL now", "targets": [w.wk["a"]], "retries": 3}).json()
    w.owner.post(f"/orgs/{w.a}/jobs/{f['id']}/approve", json={})
    f = done(w, w.a, f["id"])
    assert f["status"] == "failed" and f["runs"][0]["exit_code"] == 1 and f["runs"][0]["attempt"] == 1   # a failing command is not re-run
    w.fake.unreachable = 99
    u = w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "services.list", "targets": [w.wk["a"]]}).json()
    assert done(w, w.a, u["id"])["runs"][0]["status"] == "unreachable"


def test_cancel_a_running_job(w):
    j = w.owner.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "SLOW", "targets": [w.wk["a"]], "timeout": 600}).json()
    w.owner.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={})
    time.sleep(0.8)
    w.owner.post(f"/orgs/{w.a}/jobs/{j['id']}/cancel")
    assert done(w, w.a, j["id"])["status"] == "cancelled"


def test_script_library_is_versioned_and_risk_cannot_be_self_declared(w):
    _, tech = w.mint("technician", [w.a])
    _, adm = w.mint("administrator", [w.a])
    s = tech.post(f"/orgs/{w.a}/scripts", json={"name": "cleanup", "shell": "powershell", "body": "Get-Date", "risk": "read"}).json()
    assert s["risk"] == "modify"                                                               # a technician cannot label their own script read-only
    s2 = adm.post(f"/orgs/{w.a}/scripts", json={"name": "check", "shell": "powershell", "body": "Get-Date", "risk": "read"}).json()
    assert s2["risk"] == "read"
    j = tech.post(f"/orgs/{w.a}/jobs", json={"script_id": s2["id"], "targets": [w.wk["a"]]}).json()
    assert j["status"] == "queued" or j["status"] == "running" or j["status"] == "completed"
    tech.post(f"/orgs/{w.a}/scripts/{s2['id']}/versions", json={"body": "Remove-Item C:\\x -Recurse"})
    assert w.db.one("SELECT risk FROM scripts WHERE id=?", (s2["id"],))["risk"] == "modify"     # edited by a technician -> needs review again
    v = adm.get(f"/orgs/{w.a}/scripts/{s2['id']}/versions").json()
    assert [x["version"] for x in v] == [2, 1] and v[0]["sha256"] != v[1]["sha256"]
    pinned = adm.post(f"/orgs/{w.a}/jobs", json={"script_id": s2["id"], "script_version": 1, "targets": [w.wk["a"]]}).json()
    assert pinned["script_version"] == 1
    assert adm.get(f"/orgs/{w.b}/scripts").status_code == 403


def test_schedules_run_pre_approved_work_and_are_confined(w):
    _, adm = w.mint("administrator", [w.a])
    _, tech = w.mint("technician", [w.a])
    assert tech.post(f"/orgs/{w.a}/schedules", json={"title": "x", "targets": [w.wk["a"]], "every_minutes": 60, "template": "services.list"}).status_code == 403
    assert adm.post(f"/orgs/{w.a}/schedules", json={"title": "x", "targets": [w.wk["a"]], "every_minutes": 1, "template": "services.list"}).status_code == 422
    assert adm.post(f"/orgs/{w.a}/schedules", json={"title": "x", "targets": [w.wk["b"]], "every_minutes": 60, "template": "services.list"}).status_code == 422
    s = adm.post(f"/orgs/{w.a}/schedules", json={"title": "Nightly inventory", "targets": [w.wk["a"]], "every_minutes": 60, "template": "inventory.system"}).json()
    assert s["enabled"] is True and w.ops.tick(time.time()) == 0
    assert w.ops.tick(time.time() + 3700) == 1
    j = w.owner.get(f"/orgs/{w.a}/jobs").json()[0]
    assert j["created_by"] == "schedule:" + s["id"] and done(w, w.a, j["id"])["status"] == "completed"
    # a schedule that changes machines is NOT live until a different person approves it (four-eyes), even if the creator is an administrator
    m = adm.post(f"/orgs/{w.a}/schedules", json={"title": "restart spooler", "targets": [w.wk["a"]], "every_minutes": 60, "template": "service.restart", "params": {"name": "Spooler"}}).json()
    w.ops.tick(time.time() + 99999)
    assert m["enabled"] is False and not [j for j in w.owner.get(f"/orgs/{w.a}/jobs").json() if j["created_by"] == "schedule:" + m["id"]]
    assert adm.post(f"/orgs/{w.a}/schedules/{m['id']}/approve").status_code == 403           # not by its author
    _, adm2 = w.mint("administrator", [w.a])
    assert adm2.post(f"/orgs/{w.a}/schedules/{m['id']}/approve").json()["enabled"] is True
    w.ops.tick(time.time() + 199999)
    assert [j for j in w.owner.get(f"/orgs/{w.a}/jobs").json() if j["created_by"] == "schedule:" + m["id"]]


def test_inventory_collection_parses_and_stores_latest(w):
    r = w.owner.post(f"/workers/{w.wk['a']}/inventory/inventory.system").json()
    assert r["status"] == "ok" and r["data"] == {"host": "PC1", "ram_gb": 16}
    assert w.owner.get(f"/workers/{w.wk['a']}/inventory/inventory.system").json()["data"]["host"] == "PC1"
    assert w.owner.post(f"/workers/{w.wk['a']}/inventory/nope").status_code == 422
    _, tb = w.mint("technician", [w.b])
    assert tb.post(f"/workers/{w.wk['a']}/inventory/inventory.system").status_code == 403
    assert all("citcheck" not in c[2] for c in w.fake.calls)
    assert w.fake.calls[0][2] == "/clients" and w.fake.calls[1][2].startswith("/clients/org_")   # per-client workspace on the machine
