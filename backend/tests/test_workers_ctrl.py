import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from app.transports.agent import WorkerClient
from app.transports.base import TransportError
from app.workers import Workers
from tests.test_tasks import XorProtector
from tests.test_worker import boot, cw, fakebin, mkclient  # noqa: F401


@pytest.fixture
def ctrl(tmp_path, fakebin):
    s = Settings(data_dir=tmp_path / "ctrl", api_token="t")
    core = Core(s, Db(tmp_path / "ctrl.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    c = TestClient(app, headers={"Authorization": "Bearer t"})
    w, srv, fp = boot(tmp_path / "w")
    port = srv.server_address[1]
    spaced = " ".join(fp[i:i + 8] for i in range(0, 64, 8)).upper()
    yield type("C", (), dict(c=c, core=core, app=app, w=w, srv=srv, fp=fp, spaced=spaced, port=port, tmp=tmp_path, s=s))
    w.stop.set(); w.cancel_everything(); srv.shutdown()


def add(ctrl, **kw):
    return ctrl.c.post("/workers", json={"name": "Site PC", "address": f"127.0.0.1:{ctrl.port}", "fingerprint": ctrl.spaced, **kw})


def enroll(ctrl):
    wid = add(ctrl).json()["id"]
    r = ctrl.c.post(f"/workers/{wid}/enroll", json={"code": ctrl.w.new_enroll_code()})
    assert r.status_code == 200, r.text
    return wid


def test_public_address_and_bad_fingerprint_rejected(ctrl):
    assert ctrl.c.post("/workers", json={"name": "x", "address": "8.8.8.8:8777", "fingerprint": "ab" * 32}).status_code == 422
    assert ctrl.c.post("/workers", json={"name": "x", "address": "100.64.1.2", "fingerprint": "ab" * 32}).status_code == 201  # Tailscale ok
    assert ctrl.c.post("/workers", json={"name": "x", "address": "127.0.0.1", "fingerprint": "abc" * 22}).status_code == 422
    assert any(a["action"] == "worker.rejected" for a in ctrl.core.audit_log())


def test_enroll_flow_status_and_resources(ctrl):
    wid = add(ctrl).json()["id"]
    bad = ctrl.c.post(f"/workers/{wid}/enroll", json={"code": "00000000"})
    assert bad.status_code == 410  # no code issued on the machine yet
    ctrl.w.new_enroll_code()
    assert ctrl.c.post(f"/workers/{wid}/enroll", json={"code": "00000000"}).status_code == 403
    w = ctrl.c.post(f"/workers/{wid}/enroll", json={"code": ctrl.w.new_enroll_code()}).json()
    assert w["status"] == "online" and w["online"] and w["token_persisted"] and 0 <= w["resources"]["cpu"] <= 100
    assert "tools" in ctrl.c.get(f"/workers/{wid}/w/status").json()
    tok = ctrl.app.state.workers.clients[wid].token
    assert tok and tok not in str(ctrl.c.get("/workers").json()) and tok not in str(ctrl.core.audit_log())  # the credential VALUE is never exposed or logged
    assert tok not in str(ctrl.w.store.all("SELECT * FROM audit"))


def test_dashboard_states_online_needs_pairing_reconnecting_offline(ctrl):
    wid = add(ctrl).json()["id"]
    assert ctrl.c.get("/workers").json()[0]["state"] == "needs_pairing"  # registered, never enrolled: no credential
    assert ctrl.c.post(f"/workers/{wid}/enroll", json={"code": ctrl.w.new_enroll_code()}).json()["online"]
    assert next(x for x in ctrl.c.get("/workers").json() if x["id"] == wid)["state"] == "online"
    mgr = ctrl.app.state.workers
    mgr.state[wid] = {"online": False, "status": None, "seen": time.time() - 30}  # just lost: the monitor keeps retrying
    assert next(x for x in ctrl.c.get("/workers").json() if x["id"] == wid)["state"] == "reconnecting"
    mgr.state[wid] = {"online": False, "status": None, "seen": time.time() - 3600}
    assert next(x for x in ctrl.c.get("/workers").json() if x["id"] == wid)["state"] == "offline"


def test_proxy_allowlist_and_audit_never_logs_secret_values(ctrl):
    wid = enroll(ctrl)
    assert ctrl.c.get(f"/workers/{wid}/w/audit").status_code == 200
    for bad in ("../etc/passwd", "enroll", "clients/../x", "shell", "credentials/anything"):
        assert ctrl.c.get(f"/workers/{wid}/w/{bad}").status_code in (403, 404), bad
    ctrl.c.post(f"/workers/{wid}/w/clients", json={"id": "acme", "name": "Acme"})
    assert ctrl.c.put(f"/workers/{wid}/w/clients/acme/secrets/DB_PASSWORD", json={"value": "hunter2-controller"}).status_code == 200
    assert "hunter2-controller" not in str(ctrl.core.audit_log()) and "hunter2-controller" not in str(ctrl.w.store.all("SELECT * FROM audit"))
    assert any(a["action"] == "worker.call" and "secrets/DB_PASSWORD" in a["detail"]["path"] for a in ctrl.core.audit_log())


def test_credential_survives_controller_restart(ctrl):
    wid = enroll(ctrl)
    blob = ctrl.core.db.one("SELECT blob FROM credentials WHERE device_id=?", (f"worker:{wid}",))["blob"]
    assert b"-" not in blob[:0] and ctrl.w.credentials()[0]["id"].encode() not in blob  # stored protected, not in clear
    again = Workers(ctrl.core)  # a fresh controller process
    assert again.poll_one(wid) and again.get(wid)["online"]


def test_offline_detection_and_automatic_reconnect(ctrl):
    wid = enroll(ctrl)
    ctrl.srv.shutdown(); ctrl.srv.server_close()
    ws = ctrl.app.state.workers
    assert ws.poll_once()[wid] is False and ctrl.c.get(f"/workers/{wid}").json()["status"] == "offline"
    r = ctrl.c.get(f"/workers/{wid}/w/status")
    assert r.status_code == 502 and "unreachable" in r.text
    srv2 = cw.serve(ctrl.w, "127.0.0.1", ctrl.port)[0]  # the site's network comes back; same identity
    try:
        assert ws.poll_once()[wid] is True
        assert ctrl.c.get(f"/workers/{wid}").json()["status"] == "online"
        acts = [a["action"] for a in ctrl.core.audit_log()]
        assert "worker.offline" in acts and "worker.reconnected" in acts
    finally:
        srv2.shutdown()


def test_reads_are_retried_through_a_short_interruption(ctrl):
    wid = enroll(ctrl)
    ctrl.srv.shutdown(); ctrl.srv.server_close()
    holder = {}
    threading.Timer(0.6, lambda: holder.setdefault("srv", cw.serve(ctrl.w, "127.0.0.1", ctrl.port)[0])).start()
    t0 = time.time()
    r = ctrl.c.get(f"/workers/{wid}/w/status")  # backoff 0.3 + 0.6 + 1.2 s covers the 0.6 s outage
    assert r.status_code == 200 and time.time() - t0 >= 0.5
    holder["srv"].shutdown()


def test_writes_are_not_blindly_retried(ctrl):
    wid = enroll(ctrl)
    ctrl.srv.shutdown(); ctrl.srv.server_close()
    t0 = time.time()
    assert ctrl.c.post(f"/workers/{wid}/w/clients", json={"id": "acme", "name": "A"}).status_code == 502
    assert time.time() - t0 < 1.0  # fails fast: a retried POST could duplicate a task


def test_different_machine_on_the_same_address_is_refused(ctrl, tmp_path):
    wid = enroll(ctrl)
    ctrl.srv.shutdown(); ctrl.srv.server_close()
    imposter, isrv, _ = boot(tmp_path / "imposter", ctrl.port)  # other identity, same IP:port
    try:
        r = ctrl.c.get(f"/workers/{wid}/w/status")
        assert r.status_code in (403, 502)
        assert ctrl.app.state.workers.poll_once()[wid] is False
        assert imposter.store.one("SELECT COUNT(*) n FROM credentials")["n"] == 0
    finally:
        imposter.stop.set(); isrv.shutdown()


def test_controller_emergency_stop_reaches_the_worker(ctrl):
    wid = enroll(ctrl)
    ctrl.c.post(f"/workers/{wid}/w/clients", json={"id": "acme", "name": "Acme"})
    assert not ctrl.w.locked()
    assert ctrl.c.post("/emergency-stop").status_code == 200
    assert "emergency stop" in ctrl.w.locked()
    assert ctrl.c.get(f"/workers/{wid}/w/clients").status_code == 423


def test_remove_revokes_the_credential_on_the_worker(ctrl):
    wid = enroll(ctrl)
    assert ctrl.w.credentials()[0]["revoked"] == 0
    assert ctrl.c.delete(f"/workers/{wid}").status_code == 200
    assert ctrl.w.credentials()[0]["revoked"] == 1 and ctrl.c.get("/workers").json() == []
    assert ctrl.core.db.one("SELECT 1 FROM credentials WHERE device_id=?", (f"worker:{wid}",)) is None


def test_full_task_flow_through_the_controller(ctrl):
    wid = enroll(ctrl)
    p = f"/workers/{wid}/w"
    ctrl.c.post(f"{p}/clients", json={"id": "acme", "name": "Acme"}); ctrl.c.post(f"{p}/clients/acme/projects", json={"name": "app"})
    t = ctrl.c.post(f"{p}/tasks", json={"client_id": "acme", "project": "app", "kind": "claude", "instruction": "hello"}).json()
    assert t["status"] == "awaiting_approval"
    assert ctrl.c.post(f"{p}/tasks/{t['id']}/approve", json={"approver": "Technician"}).json()["status"] == "queued"
    for _ in range(100):
        d = ctrl.c.get(f"{p}/tasks/{t['id']}").json()
        if d["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert d["status"] == "completed_unverified" and d["events"]
    assert ctrl.c.get(f"{p}/tasks/{t['id']}?since={d['last_seq']}").json()["events"] == []


def test_worker_routes_require_token(ctrl):
    anon = TestClient(ctrl.app)
    assert anon.get("/workers").status_code == 401 and anon.get("/workers/x/w/status").status_code == 401
