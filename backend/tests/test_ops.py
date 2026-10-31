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


def test_an_administrator_cannot_launder_approval_through_a_principal_they_created_or_rotated(w):
    adm_info, adm = w.mint("administrator", [w.a])
    adm2_info, adm2 = w.mint("administrator", [w.a])
    made = adm.post("/principals", json={"name": "puppet", "role": "technician", "org_ids": [w.a]}).json()
    puppet = TestClient(w.app, headers={"Authorization": "Bearer " + made["token"]})
    j = puppet.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "echo x", "targets": [w.wk["a"]]}).json()
    assert adm.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 403      # minted the creator: not a second person
    assert adm2.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 200     # a genuinely different administrator may
    other, _ = w.mint("technician", [w.a])
    rot = adm.post(f"/principals/{other['id']}/rotate").json()["token"]                       # rotating gives the rotator the token
    rotated = TestClient(w.app, headers={"Authorization": "Bearer " + rot})
    j2 = rotated.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "echo y", "targets": [w.wk["a"]]}).json()
    assert adm.post(f"/orgs/{w.a}/jobs/{j2['id']}/approve", json={}).status_code == 403
    inv = adm.post(f"/orgs/{w.a}/invitations", json={"role": "technician"}).json()["token"]
    red = TestClient(w.app).post("/invitations/accept", json={"token": inv, "name": "Invited"}).json()
    j3 = TestClient(w.app, headers={"Authorization": "Bearer " + red["token"]}).post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "echo z", "targets": [w.wk["a"]]}).json()
    assert adm.post(f"/orgs/{w.a}/jobs/{j3['id']}/approve", json={}).status_code == 403      # the inviter counts too
    assert w.owner.post(f"/orgs/{w.a}/jobs/{j3['id']}/approve", json={}).status_code == 200


def test_control_history_is_append_only_and_transitive(w):
    adm_info, adm = w.mint("administrator", [w.a])
    adm2_info, adm2 = w.mint("administrator", [w.a])
    p = adm.post("/principals", json={"name": "p", "role": "technician", "org_ids": [w.a]}).json()
    w.owner.post(f"/principals/{p['id']}/rotate")                                           # a later rotation by someone else must not erase the creator
    tok = adm2.post(f"/principals/{p['id']}/rotate").json()["token"]
    puppet = TestClient(w.app, headers={"Authorization": "Bearer " + tok})
    j = puppet.post(f"/orgs/{w.a}/jobs", json={"shell": "cmd", "body": "echo x", "targets": [w.wk["a"]]}).json()
    assert adm.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 403       # original creator still blocked
    assert adm2.post(f"/orgs/{w.a}/jobs/{j['id']}/approve", json={}).status_code == 403      # latest rotator blocked too
    assert len(w.db.all("SELECT * FROM principal_controllers WHERE principal_id=?", (p["id"],))) == 3
    # transitive: adm minted v1 (a technician); v1 cannot be used to launder, and adm cannot approve what v1's own minted principals do
    inner = w.db.one("SELECT 1 AS x")  # placeholder to keep the fixture alive
    w.db.execute("INSERT INTO principal_controllers(principal_id,controller_id,reason,at) VALUES('usr_b','usr_a','created',1)")
    w.db.execute("INSERT INTO principal_controllers(principal_id,controller_id,reason,at) VALUES('usr_c','usr_b','created',1)")
    assert w.ops._controls("usr_c", "usr_a") is True and w.ops._controls("usr_c", "usr_z") is False


def test_principals_with_unknown_provenance_fail_closed_and_history_is_backfilled(w, tmp_path):
    adm_info, adm = w.mint("administrator", [w.a])
    w.db.execute("INSERT INTO principals(id,name,role,org_ids,token_hash,created_at) VALUES('usr_legacy','old','technician',?,?,1.0)", (json.dumps([w.a]), "h" * 64))
    w.db.execute("INSERT INTO jobs(id,org_id,title,shell,body,risk,status,approval,created_by,created_at) VALUES('job_legacy',?,'t','cmd','echo','modify','pending_approval','pending','usr_legacy',1.0)", (w.a,))
    w.db.execute("INSERT INTO job_runs(id,job_id,worker_id,status) VALUES('run_l','job_legacy',?, 'pending')", (w.wk["a"],))
    assert adm.post(f"/orgs/{w.a}/jobs/job_legacy/approve", json={}).status_code == 403          # unknown provenance: only the owner
    assert w.owner.post(f"/orgs/{w.a}/jobs/job_legacy/approve", json={}).status_code == 200
    # backfill from the older single columns on migration
    from app.db import Db
    w.db.execute("INSERT INTO principals(id,name,role,org_ids,token_hash,created_at,created_by,rotated_by) VALUES('usr_v5','n','technician','[]',?,1.0,'usr_x','usr_y')", ("k" * 64,))
    Db(w.app.state.settings.data_dir / "c.sqlite3") if False else None
    w.db.conn.executescript("DELETE FROM principal_controllers WHERE principal_id='usr_v5'")
    w.db.conn.commit()
    d2 = Db(tmp_path / "c.sqlite3")  # same database file as the fixture: re-running init performs the backfill
    got = {(r["controller_id"], r["reason"]) for r in d2.all("SELECT * FROM principal_controllers WHERE principal_id='usr_v5'")}
    assert got == {("usr_x", "created"), ("usr_y", "rotated")}


def test_four_eyes_check_is_symmetric(w):
    # an approver who was themselves created by the job's creator is not an independent second person either
    adm_info, adm = w.mint("administrator", [w.a])
    w.db.execute("INSERT INTO principal_controllers(principal_id,controller_id,reason,at) VALUES(?,?,?,1)", (adm_info["id"], "usr_creator", "created"))
    w.db.execute("INSERT INTO principals(id,name,role,org_ids,token_hash,created_at,created_by) VALUES('usr_creator','c','technician',?,?,1.0,'owner')", (json.dumps([w.a]), "z" * 64))
    w.db.execute("INSERT INTO principal_controllers(principal_id,controller_id,reason,at) VALUES('usr_creator','owner','created',1)")
    w.db.execute("INSERT INTO jobs(id,org_id,title,shell,body,risk,status,approval,created_by,created_at) VALUES('job_sym',?,'t','cmd','echo','modify','pending_approval','pending','usr_creator',1.0)", (w.a,))
    w.db.execute("INSERT INTO job_runs(id,job_id,worker_id,status) VALUES('run_s','job_sym',?,'pending')", (w.wk["a"],))
    assert adm.post(f"/orgs/{w.a}/jobs/job_sym/approve", json={}).status_code == 403
