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
def world(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha Ltd"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta Co"}).json()["id"]
    site_a = owner.post(f"/orgs/{a}/sites", json={"name": "HQ"}).json()["id"]
    dev = {}
    for key, org in (("a", a), ("b", b)):
        d = core.create_device(f"{key}-pc", "10.0.0.1", "mock", "Owner", "o@x.test", "test authorization", scenario="healthy")
        did = d["id"] if isinstance(d, dict) else d
        owner.put(f"/devices/{did}/assign", json={"org_id": org})
        dev[key] = did
    wk = {}
    for key, org in (("a", a), ("b", b)):
        wid = f"wst_{key}00000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)",
                        (wid, f"{key}-worker", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org))
        wk[key] = wid
    unassigned = core.create_device("loose", "10.0.0.9", "mock", "Owner", "o@x.test", "test authorization", scenario="healthy")
    unassigned = unassigned["id"] if isinstance(unassigned, dict) else unassigned

    def mint(role, orgs):
        r = owner.post("/principals", json={"name": f"{role}-{orgs}", "role": role, "org_ids": orgs})
        assert r.status_code == 201, r.text
        return r.json(), TestClient(app, headers={"Authorization": f"Bearer {r.json()['token']}"})
    return type("W", (), dict(app=app, core=core, owner=owner, a=a, b=b, site_a=site_a, dev=dev, wk=wk, loose=unassigned, mint=mint))


def test_owner_sees_everything_and_existing_behaviour_is_unchanged(world):
    assert {o["name"] for o in world.owner.get("/orgs").json()} == {"Alpha Ltd", "Beta Co"}
    assert world.owner.get(f"/devices/{world.dev['b']}").status_code == 200
    assert len(world.owner.get("/devices").json()) >= 3
    assert world.owner.get("/workers").status_code == 200


def test_technician_is_confined_to_own_organization(world):
    info, t = world.mint("technician", [world.a])
    assert t.get(f"/devices/{world.dev['a']}").status_code == 200
    assert t.get(f"/orgs/{world.a}/devices").json()[0]["id"] == world.dev["a"]
    assert t.get(f"/orgs/{world.a}/workers").status_code == 200
    # everything of the other client, in every shape of route, is denied with the same answer as a nonexistent id
    for path in (f"/devices/{world.dev['b']}", f"/devices/{world.dev['b']}/info", f"/orgs/{world.b}/devices", f"/orgs/{world.b}",
                 f"/workers/{world.wk['b']}", f"/workers/{world.wk['b']}/w/status", f"/devices/dev_doesnotexist"):
        r = t.get(path)
        assert r.status_code == 403 and r.json() == {"detail": "not permitted"}, path
    assert t.put(f"/devices/{world.dev['b']}/fs/write", json={"path": "x", "content_b64": ""}).status_code == 403
    assert t.get(f"/devices/{world.loose}").status_code == 403  # unassigned resources are owner-only


def test_scoped_principal_default_deny_on_global_routes(world):
    _, t = world.mint("technician", [world.a])
    for path in ("/devices", "/workers", "/audit", "/tasks", "/projects", "/principals", "/local-device", "/connections/local"):
        assert t.get(path).status_code == 403, path
    assert t.get("/orgs").json() == [o for o in world.owner.get("/orgs").json() if o["id"] == world.a]  # list is filtered
    assert t.get("/status").status_code != 403 and t.get("/features").status_code != 403  # non-tenant read-only info stays open
    assert t.post("/emergency-stop").status_code == 403


def test_owner_only_operations_cannot_be_done_by_scoped_principals(world):
    _, t = world.mint("technician", [world.a])
    assert t.post("/orgs", json={"name": "Mine"}).status_code == 403
    assert t.post(f"/orgs/{world.a}/sites", json={"name": "x"}).status_code == 403
    assert t.put(f"/devices/{world.dev['a']}/assign", json={"org_id": world.b}).status_code == 403  # cannot move a device into another client
    assert t.post("/principals", json={"name": "me", "role": "technician", "org_ids": [world.a, world.b]}).status_code == 403
    assert world.owner.post("/principals", json={"name": "x", "role": "owner", "org_ids": [world.a]}).status_code == 422  # no owner escalation
    assert world.owner.post("/principals", json={"name": "x", "role": "technician", "org_ids": []}).status_code == 422  # no unrestricted principals
    assert world.owner.post("/principals", json={"name": "x", "role": "technician", "org_ids": ["org_nope"]}).status_code == 422


def test_viewer_is_read_only(world):
    _, v = world.mint("viewer", [world.a])
    assert v.get(f"/devices/{world.dev['a']}").status_code == 200
    assert v.post(f"/devices/{world.dev['a']}/notes", json={"author": "v", "body": "hi"}).status_code == 403
    assert v.put(f"/devices/{world.dev['a']}/fs/write", json={"path": "x", "content_b64": ""}).status_code == 403


def test_token_lifecycle_hash_only_revocation_and_bad_tokens(world):
    info, t = world.mint("technician", [world.a])
    tok = t.headers["Authorization"].split()[1]
    rows = world.core.db.all("SELECT * FROM principals")
    assert tok not in json.dumps(rows) and tok not in json.dumps(world.core.db.all("SELECT * FROM audit"))  # stored as a hash only, never logged
    assert tok not in json.dumps(world.owner.get("/principals").json())
    assert t.get(f"/devices/{world.dev['a']}").status_code == 200
    assert world.owner.delete(f"/principals/{info['id']}").status_code == 200
    assert t.get(f"/devices/{world.dev['a']}").status_code == 401  # revoked at once
    assert TestClient(world.app, headers={"Authorization": "Bearer cit_garbage"}).get("/orgs").status_code == 401
    assert TestClient(world.app).get("/orgs").status_code == 401


def test_denials_and_management_are_in_the_tamper_evident_audit(world):
    info, t = world.mint("technician", [world.a])
    t.get(f"/devices/{world.dev['b']}")
    log = world.core.db.all("SELECT actor,action,detail FROM audit")
    assert any(r["actor"] == info["id"] and r["action"] == "tenancy.denied" and world.dev["b"] in r["detail"] for r in log)
    assert any(r["action"] == "principal.created" for r in log) and any(r["action"] == "device.assigned" for r in log)
    assert world.core.db.verify_audit_chain()


def test_site_must_belong_to_the_organization(world):
    r = world.owner.put(f"/devices/{world.dev['b']}/assign", json={"org_id": world.b, "site_id": world.site_a})
    assert r.status_code == 422
    assert world.owner.put(f"/devices/{world.dev['a']}/assign", json={"org_id": world.a, "site_id": world.site_a}).status_code == 200
    assert world.owner.get(f"/orgs/{world.a}/devices").json()[0]["site_id"] == world.site_a


def test_migration_adds_columns_to_an_old_database(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite3"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, transport TEXT NOT NULL, owner_name TEXT NOT NULL,"
                    "owner_contact TEXT NOT NULL, authorization_note TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);"
                    "CREATE TABLE workers(id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);"
                    "INSERT INTO devices VALUES('dev_old','old','1.2.3.4','mock','o','c','n','paired',1.0);")
    c.commit(); c.close()
    db = Db(p)
    assert db.one("SELECT org_id, site_id FROM devices WHERE id='dev_old'") == {"org_id": None, "site_id": None}  # old rows survive, unassigned
    Db(p)  # idempotent


def test_fleet_view_groups_by_site_and_is_scoped(world):
    world.owner.put(f"/devices/{world.dev['a']}/assign", json={"org_id": world.a, "site_id": world.site_a})
    _, t = world.mint("technician", [world.a])
    f = t.get(f"/orgs/{world.a}/fleet").json()
    hq = next(s for s in f["sites"] if s["id"] == world.site_a)
    assert [d["id"] for d in hq["devices"]] == [world.dev["a"]]
    assert hq["devices"][0]["path"] in ("lan", "loopback", "overlay", "link-local")
    nosite = next(s for s in f["sites"] if s["id"] is None)
    assert [w["id"] for w in nosite["workers"]] == [world.wk["a"]] and nosite["workers"][0]["state"] in ("needs_pairing", "offline", "online", "reconnecting")
    assert world.dev["b"] not in json.dumps(f) and world.wk["b"] not in json.dumps(f)  # nothing of the other client leaks
    assert t.get(f"/orgs/{world.b}/fleet").status_code == 403


def test_onboarding_has_no_secrets_or_fixed_addresses_and_validates_kind(world):
    _, t = world.mint("technician", [world.a])
    r = t.get(f"/orgs/{world.a}/onboarding").json()
    text = json.dumps(r)
    assert "cit-worker.exe" in text and "public Internet" in text
    import re
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
    assert t.get(f"/orgs/{world.a}/onboarding?kind=nope").status_code == 422
    assert t.get(f"/orgs/{world.b}/onboarding").status_code == 403


def test_path_classification():
    from app.netpolicy import classify_path
    assert classify_path("127.0.0.1") == "loopback" and classify_path("192.168.1.5") == "lan" and classify_path("100.101.102.103") == "overlay"
    assert classify_path("8.8.8.8") == "blocked" and classify_path("169.254.1.1") == "link-local"
