"""Roles (administrator/technician/viewer/customer), invitations, groups/tags, offboarding, rotation, brute-force limiting."""
import json
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
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha Ltd"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta Co"}).json()["id"]
    wk = {}
    for key, org in (("a", a), ("b", b)):
        wid = f"wst_{key}00000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)", (wid, f"{key}-w", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org))
        wk[key] = wid

    def mint(role, orgs):
        r = owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs})
        assert r.status_code == 201, r.text
        return r.json(), TestClient(app, headers={"Authorization": f"Bearer {r.json()['token']}"})
    return type("W", (), dict(app=app, core=core, db=core.db, owner=owner, a=a, b=b, wk=wk, mint=mint))


def test_administrator_manages_only_own_client(w):
    _, adm = w.mint("administrator", [w.a])
    assert adm.post(f"/orgs/{w.a}/sites", json={"name": "HQ"}).status_code == 201
    assert adm.post(f"/orgs/{w.b}/sites", json={"name": "x"}).status_code == 403
    assert adm.post("/orgs", json={"name": "Mine"}).status_code == 403          # only the owner creates clients
    assert adm.post(f"/orgs/{w.a}/offboard", json={"confirm_name": "Alpha Ltd"}).status_code == 403
    assert adm.put(f"/workers/{w.wk['a']}/assign", json={"org_id": w.b}).status_code == 403  # cannot hand a machine to another client
    assert adm.put(f"/workers/{w.wk['b']}/assign", json={"org_id": w.a}).status_code == 403  # nor take one
    assert adm.put(f"/workers/{w.wk['a']}/tags", json={"tags": ["Server", "server", " Lab "]}).json() == {"tags": ["lab", "server"]}
    assert adm.put(f"/workers/{w.wk['b']}/tags", json={"tags": ["x"]}).status_code == 403


def test_administrator_principal_management_is_confined(w):
    _, adm = w.mint("administrator", [w.a])
    r = adm.post("/principals", json={"name": "t", "role": "technician", "org_ids": [w.a]})
    assert r.status_code == 201
    assert adm.post("/principals", json={"name": "t", "role": "technician", "org_ids": [w.a, w.b]}).status_code == 403
    assert adm.post("/principals", json={"name": "t", "role": "administrator", "org_ids": [w.a]}).status_code == 403  # no administrator cloning
    other, _ = w.mint("technician", [w.b])
    assert other["id"] not in [p["id"] for p in adm.get("/principals").json()]
    assert adm.delete(f"/principals/{other['id']}").status_code == 403
    assert adm.post(f"/principals/{other['id']}/rotate").status_code == 403
    assert adm.delete(f"/principals/{r.json()['id']}").status_code == 200


def test_technician_viewer_cannot_administer(w):
    _, t = w.mint("technician", [w.a])
    _, v = w.mint("viewer", [w.a])
    for c in (t, v):
        assert c.post(f"/orgs/{w.a}/invitations", json={"role": "viewer"}).status_code == 403
        assert c.get(f"/orgs/{w.a}/audit").status_code == 403
        assert c.get("/principals").status_code == 403
        assert c.put(f"/workers/{w.wk['a']}/tags", json={"tags": ["x"]}).status_code == 403
    assert t.get("/me").json()["role"] == "technician"


def test_customer_sees_only_own_summary_never_machines_or_commands(w):
    _, c = w.mint("customer", [w.a])
    assert c.get(f"/orgs/{w.a}/fleet").status_code == 200
    assert c.get(f"/orgs/{w.b}/fleet").status_code == 403
    for path in (f"/workers/{w.wk['a']}", f"/workers/{w.wk['a']}/w/status", f"/orgs/{w.a}/audit", f"/orgs/{w.a}/sites", "/devices", "/audit", "/principals", f"/orgs/{w.a}/invitations"):
        assert c.get(path).status_code == 403, path
    assert c.post(f"/workers/{w.wk['a']}/w/clients/x/exec", json={"argv": ["cmd"]}).status_code == 403
    assert c.get("/orgs").json() == [o for o in w.owner.get("/orgs").json() if o["id"] == w.a]


def test_invitation_is_single_use_expiring_and_hash_only(w):
    _, adm = w.mint("administrator", [w.a])
    inv = adm.post(f"/orgs/{w.a}/invitations", json={"role": "technician"}).json()
    anon = TestClient(w.app)
    r = anon.post("/invitations/accept", json={"token": inv["token"], "name": "Dana"})
    assert r.status_code == 201 and r.json()["role"] == "technician" and r.json()["org_ids"] == [w.a]
    me = TestClient(w.app, headers={"Authorization": "Bearer " + r.json()["token"]}).get("/me").json()
    assert me["org_ids"] == [w.a]
    assert anon.post("/invitations/accept", json={"token": inv["token"], "name": "Again"}).status_code == 410  # single use
    assert inv["token"] not in json.dumps(w.db.all("SELECT * FROM invitations")) and inv["token"] not in json.dumps(w.db.all("SELECT * FROM audit"))
    inv2 = adm.post(f"/orgs/{w.a}/invitations", json={"role": "viewer", "days": 1}).json()
    w.db.execute("UPDATE invitations SET expires_at=1 WHERE id=?", (inv2["id"],))
    assert anon.post("/invitations/accept", json={"token": inv2["token"], "name": "Late"}).status_code == 410
    assert adm.post(f"/orgs/{w.a}/invitations", json={"role": "administrator"}).status_code == 403  # only the owner invites administrators
    inv3 = adm.post(f"/orgs/{w.a}/invitations", json={"role": "viewer"}).json()
    adm.delete(f"/orgs/{w.a}/invitations/{inv3['id']}")
    assert anon.post("/invitations/accept", json={"token": inv3["token"], "name": "Revoked"}).status_code == 410


def test_token_rotation_kills_the_old_token(w):
    info, t = w.mint("technician", [w.a])
    old = t.headers["Authorization"]
    new = w.owner.post(f"/principals/{info['id']}/rotate").json()["token"]
    assert TestClient(w.app, headers={"Authorization": old}).get("/me").status_code == 401
    assert TestClient(w.app, headers={"Authorization": "Bearer " + new}).get("/me").status_code == 200


def test_repeated_bad_tokens_are_rate_limited(w):
    bad = TestClient(w.app, headers={"Authorization": "Bearer nope"})
    codes = [bad.get("/me").status_code for _ in range(35)]
    assert codes[0] == 401 and codes[-1] == 429
    assert TestClient(w.app).post("/invitations/accept", json={"token": "x" * 12, "name": "n"}).status_code in (410, 429)


def test_groups_cannot_contain_another_clients_machines(w):
    _, adm = w.mint("administrator", [w.a])
    g = adm.post(f"/orgs/{w.a}/groups", json={"name": "Servers"}).json()["id"]
    assert adm.post(f"/orgs/{w.a}/groups", json={"name": "Servers"}).status_code == 409
    assert adm.put(f"/orgs/{w.a}/groups/{g}", json={"members": [{"kind": "workers", "rid": w.wk["a"]}]}).status_code == 200
    assert adm.put(f"/orgs/{w.a}/groups/{g}", json={"members": [{"kind": "workers", "rid": w.wk["b"]}]}).status_code == 422
    assert adm.get(f"/orgs/{w.a}/groups").json()[0]["members"] == [{"kind": "workers", "rid": w.wk["a"]}]
    assert adm.put(f"/orgs/{w.b}/groups/{g}", json={"members": []}).status_code == 403


def test_org_audit_contains_only_that_client(w):
    _, adm = w.mint("administrator", [w.a])
    adm.post(f"/orgs/{w.a}/sites", json={"name": "HQ"})
    w.owner.post(f"/orgs/{w.b}/sites", json={"name": "Secret site B"})
    log = json.dumps(adm.get(f"/orgs/{w.a}/audit").json())
    assert "site.created" in log and "Secret site B" not in log and w.b not in log


def test_offboarding_revokes_access_and_purge_waits_for_retention(w, monkeypatch):
    info, t = w.mint("technician", [w.a])
    multi, m = w.mint("technician", [w.a, w.b])
    monkeypatch.setattr(w.app.state.workers, "remove", lambda wid, revoke=True: None)
    assert w.owner.post(f"/orgs/{w.a}/offboard", json={"confirm_name": "wrong"}).status_code == 422
    r = w.owner.post(f"/orgs/{w.a}/offboard", json={"confirm_name": "Alpha Ltd", "retention_days": 30}).json()
    assert r["status"] == "offboarded" and r["principals_revoked"] == 1
    assert t.get("/me").status_code == 401                                  # sole-client technician: revoked at once
    assert m.get("/me").json()["org_ids"] == [w.b]                          # multi-client technician keeps only the other client
    assert m.get(f"/workers/{w.wk['a']}").status_code == 403
    assert w.owner.post(f"/orgs/{w.a}/purge", json={"confirm_name": "Alpha Ltd"}).status_code == 409  # still within retention
    w.db.execute("UPDATE orgs SET retention_until=1 WHERE id=?", (w.a,))
    assert w.owner.post(f"/orgs/{w.a}/purge", json={"confirm_name": "Alpha Ltd"}).json() == {"purged": True}
    assert not w.db.one("SELECT 1 AS x FROM orgs WHERE id=?", (w.a,)) and not w.db.one("SELECT 1 AS x FROM workers WHERE org_id=?", (w.a,))
    assert w.db.one("SELECT 1 AS x FROM workers WHERE org_id=?", (w.b,))   # the other client is untouched
    assert w.db.verify_audit_chain()
