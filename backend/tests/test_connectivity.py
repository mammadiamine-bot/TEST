import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from app.transports.agent import IdentityMismatch, WorkerClient
from app.transports.base import TransportError
from app.targetcore import TargetError
from app.workers import BACKOFF_MAX, OFFLINE_AFTER, POLL_OK, classify
from tests.test_tasks import XorProtector


class FakeClient:
    token, fp = "t", "ab" * 32

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def probe(self, path, timeout=5):
        self.calls += 1
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step()
        return step


@pytest.fixture
def w(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]
    wk = {}
    for k, org in (("a", a), ("b", b), ("c", a)):
        wid = f"wst_{k}0000000"
        core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)", (wid, f"{k}-pc", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), org)); wk[k] = wid
    mint = lambda role, orgs: TestClient(app, headers={"Authorization": "Bearer " + owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()["token"]})  # noqa: E731
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, wk=wk, ws=app.state.workers, mint=mint))


OK = {"cpu": 5, "ram": 5, "disk": 5, "worker_id": "x"}


def test_failure_classification_gives_the_operator_a_reason():
    assert classify(TransportError("cannot reach agent at h:1: timed out")) == "timeout"
    assert classify(TransportError("cannot reach agent at h:1: [WinError 10061] No connection could be made because the target machine actively refused it")) == "refused"
    assert classify(TransportError("[Errno 11001] getaddrinfo failed")) == "unreachable"
    assert classify(IdentityMismatch("agent identity mismatch")) == "identity"
    assert classify(TargetError("invalid or expired token", 401)) == "auth"
    assert classify(TransportError("[SSL: WRONG_VERSION_NUMBER] wrong version number")) == "tls"


def test_state_has_hysteresis_and_never_fakes_online(w):
    wid = w.wk["a"]; w.ws.clients[wid] = FakeClient([OK, TransportError("timed out"), TransportError("timed out"), TransportError("timed out"), OK])
    assert w.ws.poll_one(wid) and w.ws.get(wid)["state"] == "online"
    assert not w.ws.poll_one(wid)
    v = w.ws.get(wid); assert v["state"] == "degraded" and v["online"] is False and v["error_code"] == "timeout" and v["hint"]      # 1 failure: unstable, but honestly NOT online
    w.ws.poll_one(wid); assert w.ws.get(wid)["state"] == "degraded"
    w.ws.poll_one(wid); v = w.ws.get(wid); assert v["fails"] == OFFLINE_AFTER and v["state"] == "reconnecting"                       # seen recently -> reconnecting, not "online"
    assert w.ws.poll_one(wid); v = w.ws.get(wid); assert v["state"] == "online" and v["fails"] == 0 and v["last_error"] is None and v["latency_ms"] is not None
    acts = [r["action"] for r in w.db.all("SELECT action FROM audit") if r["action"].startswith("worker.")]
    assert "worker.offline" in acts and "worker.reconnected" in acts


def test_backoff_doubles_and_is_capped(w):
    wid = w.wk["a"]; w.ws.clients[wid] = FakeClient([TransportError("timed out")])
    gaps = []
    for _ in range(7):
        w.ws.poll_one(wid); gaps.append(round(w.ws.state[wid]["next_poll"] - time.time()))
    assert gaps[:3] == [10, 20, 40] and max(gaps) == BACKOFF_MAX and gaps[-1] == BACKOFF_MAX
    w.ws.clients[wid].script = [OK]; w.ws.poll_one(wid)
    assert round(w.ws.state[wid]["next_poll"] - time.time()) == POLL_OK                       # recovery resets the interval at once


def test_probe_is_short_and_never_retries(w):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()      # a closed port
    c = WorkerClient("127.0.0.1", port, "ab" * 32, "t", timeout=15)
    t0 = time.time()
    with pytest.raises(TransportError):
        c.probe("/status", 3)
    assert time.time() - t0 < 2.5                                                              # old path: 4 attempts with back-off
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); hole = srv.getsockname()[1]   # accepts the TCP connection but never answers
    c2 = WorkerClient("127.0.0.1", hole, "ab" * 32, "t", timeout=15); t0 = time.time()
    with pytest.raises(TransportError, match="timed out"):
        c2.probe("/status", 1)
    assert time.time() - t0 < 2.5; srv.close()


def test_one_sleeping_computer_does_not_delay_the_others(w):
    gate = threading.Event()
    w.ws.clients[w.wk["a"]] = FakeClient([lambda: (gate.wait(3) and None) or (_ for _ in ()).throw(TransportError("timed out"))])
    w.ws.clients[w.wk["c"]] = FakeClient([OK])
    t0 = time.time(); w.ws.tick()
    end = time.time() + 2
    while time.time() < end and not w.ws.state.get(w.wk["c"], {}).get("online"):
        time.sleep(0.02)
    assert w.ws.state[w.wk["c"]]["online"] and time.time() - t0 < 1.5                          # c answered while a was still blocked
    gate.set(); time.sleep(0.3)


def test_reconnect_resets_backoff_and_reports_the_real_result(w):
    wid = w.wk["a"]; w.ws.clients[wid] = FakeClient([TransportError("timed out")])
    for _ in range(4):
        w.ws.poll_one(wid)
    assert w.ws.state[wid]["next_poll"] - time.time() > 30
    r = w.owner.post(f"/workers/{wid}/reconnect").json(); assert r["state"] != "online" and r["error_code"] == "timeout"      # still down: it says so
    w.ws.clients[wid].script = [OK]
    r = w.owner.post(f"/workers/{wid}/reconnect").json(); assert r["state"] == "online"
    assert any(x["action"] == "worker.reconnect_requested" for x in w.db.all("SELECT action FROM audit"))


def test_diagnose_explains_the_failure_step_by_step(w):
    wid = w.wk["a"]; w.ws.clients[wid] = FakeClient([OK])
    d = w.owner.post(f"/workers/{wid}/diagnose").json()
    names = [x["step"] for x in d["steps"]]
    assert names[:2] == ["policy", "tcp"] and d["steps"][1]["ok"] is False and d["verdict"] == "refused" and d["hint"]        # 127.0.0.1:1 refuses
    assert d["steps"][1]["ms"] >= 0
    assert w.db.verify_audit_chain()


def test_connection_endpoints_respect_client_isolation(w):
    ta, tb = w.mint("technician", [w.a]), w.mint("technician", [w.b])
    assert ta.get(f"/workers/{w.wk['a']}/connection").status_code == 200
    for path in ("connection",):
        assert tb.get(f"/workers/{w.wk['a']}/{path}").status_code == 403
    assert tb.post(f"/workers/{w.wk['a']}/diagnose").status_code == 403 and tb.post(f"/workers/{w.wk['a']}/reconnect").status_code == 403
    assert w.mint("viewer", [w.a]).post(f"/workers/{w.wk['a']}/reconnect").status_code == 403           # viewers only look
