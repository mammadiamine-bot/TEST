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
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]

    def worker(org, n):
        wid = f"wst_{n:012d}"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at) VALUES(?,?,?,?,?,?)", (wid, f"pc{n}", "127.0.0.1:1", "ab" * 32, "enrolled", time.time()))
        return wid

    def mint(role, orgs):
        r = owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()
        return TestClient(app, headers={"Authorization": f"Bearer {r['token']}"})
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, worker=worker, mint=mint, billing=core.billing))


def test_new_client_starts_on_trial_and_features_are_gated(w):
    s = w.owner.get(f"/orgs/{w.a}/subscription").json()
    assert s["plan"] == "trial" and s["status"] == "trial" and "ai" in s["entitlements"]["features"]
    w.owner.put(f"/orgs/{w.a}/subscription", json={"plan": "basic"})
    # basic has no scheduler: fails closed with 402
    r = w.owner.post(f"/orgs/{w.a}/schedules", json={"title": "x", "targets": ["wst_x"], "every_minutes": 60, "template": "services.list"})
    assert r.status_code == 402
    assert w.owner.get(f"/orgs/{w.b}/subscription").json()["plan"] == "trial"      # the other client is unaffected


def test_expired_subscription_blocks_work_but_not_reading(w):
    w.db.execute("UPDATE subscriptions SET period_end=1 WHERE org_id=?", (w.a,))
    assert w.owner.get(f"/orgs/{w.a}/subscription").json()["status"] == "expired"
    assert w.owner.post(f"/orgs/{w.a}/jobs", json={"template": "services.list", "targets": ["wst_x"]}).status_code == 402
    assert w.owner.get(f"/orgs/{w.a}/alerts").status_code == 200


def test_device_limit_is_enforced_on_assignment_and_downgrade(w):
    ids = [w.worker(w.a, i) for i in range(6)]
    for i in ids[:5]:
        assert w.owner.put(f"/workers/{i}/assign", json={"org_id": w.a}).status_code == 200
    assert w.owner.put(f"/workers/{ids[5]}/assign", json={"org_id": w.a}).status_code == 402   # trial = 5 machines
    w.owner.put(f"/orgs/{w.a}/subscription", json={"plan": "professional"})
    assert w.owner.put(f"/workers/{ids[5]}/assign", json={"org_id": w.a}).status_code == 200
    for i in range(10, 16):
        wid = w.worker(w.a, i); w.db.execute("UPDATE workers SET org_id=? WHERE id=?", (w.a, wid))   # now 12 machines
    assert w.owner.put(f"/orgs/{w.a}/subscription", json={"plan": "basic"}).status_code == 409        # basic allows 10: no silent over-limit downgrade


def test_only_the_owner_changes_plans_licenses_and_invoices(w):
    adm = w.mint("administrator", [w.a]); cust = w.mint("customer", [w.a])
    for c in (adm, cust, w.mint("technician", [w.a])):
        assert c.put(f"/orgs/{w.a}/subscription", json={"plan": "business"}).status_code == 403
        assert c.post(f"/orgs/{w.a}/subscription/license", json={"plan": "business"}).status_code == 403
        assert c.post(f"/orgs/{w.a}/invoices", json={}).status_code == 403
    assert adm.get(f"/orgs/{w.a}/subscription").status_code == 200 and cust.get(f"/orgs/{w.a}/subscription").status_code == 200
    assert adm.get(f"/orgs/{w.b}/subscription").status_code == 403


def test_licenses_are_signed_client_bound_and_single_use(w):
    lic = w.owner.post(f"/orgs/{w.a}/subscription/license", json={"plan": "business", "days": 90}).json()["license"]
    assert w.owner.post(f"/orgs/{w.b}/subscription", json={"license": lic}).status_code == 403          # bound to Alpha
    tampered = lic[:-3] + ("AAA" if not lic.endswith("AAA") else "BBB")
    assert w.owner.post(f"/orgs/{w.a}/subscription", json={"license": tampered}).status_code in (403, 422)
    r = w.owner.post(f"/orgs/{w.a}/subscription", json={"license": lic})
    assert r.status_code == 200 and r.json()["plan"] == "business" and r.json()["status"] == "active"
    assert w.owner.post(f"/orgs/{w.a}/subscription", json={"license": lic}).status_code == 409           # single use
    hist = w.owner.get(f"/orgs/{w.a}/subscription/history").json()
    assert any("license" in h["change"] for h in hist) and lic not in str(hist)
    assert w.db.verify_audit_chain()


def test_usage_metering_and_invoice_draft_are_honest(w):
    w.owner.put(f"/orgs/{w.a}/subscription", json={"plan": "professional"})
    for i in range(3):
        wid = w.worker(w.a, i); w.owner.put(f"/workers/{wid}/assign", json={"org_id": w.a})
    w.billing.entitle(w.a, "jobs"); w.billing.entitle(w.a, "jobs"); w.billing.add_ai_tokens(w.a, 1234)
    u = w.owner.get(f"/orgs/{w.a}/subscription").json()["usage"]
    assert u["devices_peak"] == 3 and u["jobs"] == 2 and u["ai_tokens_month"] == 1234
    inv = w.owner.post(f"/orgs/{w.a}/invoices", json={}).json()
    assert inv["status"] == "draft" and inv["total"] == 18.0 and "nothing has been charged" in inv["note"]
    assert len(w.owner.get(f"/orgs/{w.a}/invoices").json()) == 1
    assert w.mint("customer", [w.a]).get(f"/orgs/{w.a}/invoice-data").status_code == 200


def test_ai_allowance_is_enforced(w):
    w.owner.put(f"/orgs/{w.a}/subscription", json={"plan": "professional"})
    w.billing.add_ai_tokens(w.a, 600_000)
    with pytest.raises(Exception) as e:
        w.billing.entitle(w.a, "ai")
    assert "402" in str(e.value) or "allowance" in str(e.value)
