import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.monitor import Monitor
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
    wk = {}
    for k, org in (("a", a), ("b", b)):
        wid = f"wst_{k}00000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)", (wid, f"{k}-pc", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org))
        wk[k] = wid
    sent = []
    mon = Monitor(core.db, sender=lambda to, subj, body: sent.append((to, subj, body)))
    mon.last_sample.clear()
    def mint(role, orgs):
        r = owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()
        return TestClient(app, headers={"Authorization": f"Bearer {r['token']}"})
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, wk=wk, mon=mon, sent=sent, mint=mint))


def poll(mon, wid, n, **st):
    for _ in range(n):
        mon.last_sample.clear()  # bypass the 30 s sampling interval in tests
        mon.on_poll(wid, True, {"cpu": 10, "ram": 20, "disk": 30, **st})


def test_threshold_alert_is_deduplicated_resolved_and_notified_once(w):
    w.owner.post(f"/orgs/{w.a}/alert-rules", json={"metric": "cpu", "op": ">", "threshold": 90, "consecutive": 3, "severity": "critical"})
    w.owner.post(f"/orgs/{w.a}/notify-policies", json={"target": "ops@example.test", "min_severity": "warning"})
    poll(w.mon, w.wk["a"], 2, cpu=99)
    assert w.db.all("SELECT * FROM alerts") == []                      # needs 3 consecutive breaches
    poll(w.mon, w.wk["a"], 5, cpu=99)
    al = w.db.all("SELECT * FROM alerts")
    assert len(al) == 1 and al[0]["state"] == "open" and al[0]["count"] >= 3   # deduplicated: one alert, counted
    assert len(w.sent) == 1 and w.sent[0][0] == "ops@example.test"             # one notification, not one per sample
    assert w.mon.ack(al[0]["id"], "usr_x") and w.db.one("SELECT state FROM alerts")["state"] == "acknowledged"
    poll(w.mon, w.wk["a"], 1, cpu=5)
    assert w.db.one("SELECT state FROM alerts")["state"] == "resolved"
    poll(w.mon, w.wk["a"], 3, cpu=99)
    assert len(w.db.all("SELECT * FROM alerts")) == 2 and len(w.sent) == 2    # a new incident after resolution


def test_offline_alert_and_recovery(w):
    for _ in range(3):
        w.mon.on_poll(w.wk["a"], False, None)
    a = w.db.one("SELECT * FROM alerts")
    assert a["kind"] == "offline" and a["severity"] == "critical"
    w.mon.on_poll(w.wk["a"], True, {"cpu": 1, "ram": 1, "disk": 1})
    assert w.db.one("SELECT state FROM alerts")["state"] == "resolved"


def test_unassigned_machines_are_not_monitored_and_rules_do_not_cross_clients(w):
    w.db.execute("UPDATE workers SET org_id=NULL WHERE id=?", (w.wk["b"],))
    w.mon.on_poll(w.wk["b"], False, None)
    assert w.db.all("SELECT * FROM samples") == []
    w.owner.post(f"/orgs/{w.b}/alert-rules", json={"metric": "ram", "threshold": 50, "consecutive": 1})
    poll(w.mon, w.wk["a"], 3, ram=99)
    assert w.db.all("SELECT * FROM alerts") == []                      # Beta's rule never fires for Alpha's machine


def test_notification_without_smtp_is_recorded_honestly(w):
    mon = Monitor(w.db, sender=None)
    w.owner.post(f"/orgs/{w.a}/notify-policies", json={"target": "ops@example.test", "min_severity": "info"})
    w.owner.post(f"/orgs/{w.a}/alert-rules", json={"metric": "disk", "threshold": 80, "consecutive": 1})
    mon.last_sample.clear(); mon.on_poll(w.wk["a"], True, {"cpu": 1, "ram": 1, "disk": 95})
    assert w.db.one("SELECT status FROM notifications")["status"] == "not_sent_no_smtp"


def test_retention_prunes_old_samples(w):
    w.db.execute("INSERT INTO samples(worker_id,ts,online,cpu,ram,disk) VALUES(?,?,?,?,?,?)", (w.wk["a"], 1.0, 1, 1, 1, 1))
    poll(w.mon, w.wk["a"], 1)
    w.mon.prune()
    assert all(s["ts"] > 1.0 for s in w.db.all("SELECT ts FROM samples"))


def test_api_scoping_for_alerts_metrics_reports_and_tickets(w):
    w.owner.post(f"/orgs/{w.a}/alert-rules", json={"metric": "cpu", "threshold": 50, "consecutive": 1})
    poll(w.mon, w.wk["a"], 2, cpu=99); poll(w.mon, w.wk["b"], 2)
    t, c = w.mint("technician", [w.a]), w.mint("customer", [w.a])
    al = t.get(f"/orgs/{w.a}/alerts").json()
    assert len(al) == 1 and t.get(f"/orgs/{w.b}/alerts").status_code == 403
    assert t.post(f"/orgs/{w.a}/alerts/{al[0]['id']}/ack").json() == {"acknowledged": True}
    assert t.get(f"/workers/{w.wk['a']}/metrics").status_code == 200 and t.get(f"/workers/{w.wk['b']}/metrics").status_code == 403
    rep = t.get(f"/orgs/{w.a}/reports/fleet").json()
    assert [x["worker"] for x in rep["workers"]] == [w.wk["a"]] and rep["fleet_availability_pct"] == 100.0
    assert c.get(f"/orgs/{w.a}/alerts").status_code == 200 and c.get(f"/orgs/{w.a}/reports/fleet").status_code == 200
    assert c.post(f"/orgs/{w.a}/alerts/{al[0]['id']}/ack").status_code == 403            # customers look, they do not act
    assert t.post(f"/orgs/{w.a}/alert-rules", json={"metric": "cpu", "threshold": 1}).status_code == 403   # rules are administrator work
    tk = c.post(f"/orgs/{w.a}/tickets", json={"title": "PC is slow", "priority": "high"}).json()
    assert c.post(f"/orgs/{w.a}/tickets", json={"title": "x", "worker_id": w.wk["b"]}).status_code == 422
    assert t.patch(f"/orgs/{w.a}/tickets/{tk['id']}", json={"status": "resolved"}).status_code == 200
    assert t.get(f"/orgs/{w.a}/reports/sla").json()["sla_pct"] == 100.0
    assert t.post(f"/orgs/{w.a}/alerts/{al[0]['id']}/ticket").status_code == 201
