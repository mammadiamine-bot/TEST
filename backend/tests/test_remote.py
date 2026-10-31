import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from tests.test_tasks import XorProtector


@pytest.fixture
def w(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    launched, killed = [], []
    rem = app.state.remote
    rem.popen, rem.find_pids, rem.kill, rem.exe = (lambda argv, **k: launched.append(argv)), (lambda rid: [4242] if launched else []), killed.append, (lambda: "rustdesk.exe")
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]; b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]
    wk = {}
    for k, org in (("a", a), ("b", b)):
        wid = f"wst_{k}0000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id,rustdesk_id) VALUES(?,?,?,?,?,?,?,?)", (wid, f"{k}-pc", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org, "123456789" if k == "a" else None)); wk[k] = wid
    mint = lambda role, orgs: TestClient(app, headers={"Authorization": "Bearer " + owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()["token"]})  # noqa: E731
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, wk=wk, rem=rem, launched=launched, killed=killed, mint=mint, core=core))


def test_session_start_records_operator_launches_viewer_and_audits(w):
    r = w.owner.post(f"/workers/{w.wk['a']}/remote/sessions", json={"minutes": 15})
    assert r.status_code == 201
    s = r.json()
    assert s["operator"] == "owner" and s["status"] == "active" and s["controllable"] == 1 and s["expires_at"] - s["started_at"] == pytest.approx(900, abs=2)
    assert w.launched == [["rustdesk.exe", "--connect", "123456789"]]          # only the ID; never a password
    assert any(x["action"] == "remote.started" for x in w.db.all("SELECT action FROM audit"))


def test_end_closes_the_viewer_and_expiry_is_enforced(w):
    s = w.owner.post(f"/workers/{w.wk['a']}/remote/sessions", json={"minutes": 5}).json()
    e = w.owner.post(f"/workers/{w.wk['a']}/remote/sessions/{s['id']}/end").json()
    assert e["status"] == "ended" and 4242 in w.killed and e["ended_at"]
    s2 = w.owner.post(f"/workers/{w.wk['a']}/remote/sessions", json={"minutes": 5}).json()
    w.db.execute("UPDATE remote_sessions SET expires_at=1 WHERE id=?", (s2["id"],))
    assert [x["status"] for x in w.owner.get(f"/workers/{w.wk['a']}/remote/sessions").json()][0] == "expired"     # swept on the next look
    log = [x["action"] for x in w.db.all("SELECT action FROM audit")]
    assert log.count("remote.ended") == 2 and w.db.verify_audit_chain()


def test_emergency_stop_ends_every_live_session(w):
    s = w.owner.post(f"/workers/{w.wk['a']}/remote/sessions", json={"minutes": 30}).json()
    w.rem.end_all()
    assert w.rem.get(s["id"])["status"] == "emergency_stop" and 4242 in w.killed


def test_needs_a_saved_id_and_respects_client_boundaries(w):
    assert w.owner.post(f"/workers/{w.wk['b']}/remote/sessions", json={}).status_code == 409           # no RustDesk ID saved for that computer
    ta, tb = w.mint("technician", [w.a]), w.mint("technician", [w.b])
    assert ta.post(f"/workers/{w.wk['a']}/remote/sessions", json={}).status_code == 201
    assert tb.post(f"/workers/{w.wk['a']}/remote/sessions", json={}).status_code == 403
    assert tb.get(f"/workers/{w.wk['a']}/remote/sessions").status_code == 403
    assert w.mint("viewer", [w.a]).post(f"/workers/{w.wk['a']}/remote/sessions", json={}).status_code == 403   # viewers cannot take control
    assert w.mint("customer", [w.a]).get(f"/workers/{w.wk['a']}/remote/sessions").status_code == 403
    sid = w.owner.get(f"/workers/{w.wk['a']}/remote/sessions").json()[0]["id"]
    assert tb.post(f"/workers/{w.wk['a']}/remote/sessions/{sid}/end").status_code == 403
    assert w.owner.post(f"/workers/{w.wk['a']}/remote/sessions", json={"minutes": 999}).status_code == 422
