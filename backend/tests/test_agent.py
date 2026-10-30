import importlib.util
import sys
import time
from pathlib import Path

import pytest

from app.targetcore import Grants, TargetError
from app.tools import get
from app.transports.agent import AgentClient
from app.transports.base import TransportError

spec = importlib.util.spec_from_file_location("cit_agent", Path(__file__).resolve().parents[2] / "agent" / "cit_agent.py")
cit_agent = importlib.util.module_from_spec(spec); sys.modules["cit_agent"] = cit_agent; spec.loader.exec_module(cit_agent)


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"; p.mkdir(); (p / "a.txt").write_text("hello"); return p


def start(proj, grants=Grants(), minutes=5, ps=None, roots=True):
    log = []
    ag = cit_agent.Agent([proj] if roots else [], grants, minutes, say=log.append, ps_runner=ps)
    srv, fp = cit_agent.serve(ag, "127.0.0.1", 0)
    return ag, srv, fp, srv.server_address[1], log


@pytest.fixture
def paired(proj):
    ag, srv, fp, port, log = start(proj, Grants(terminal=True, write=True, claude=True))
    c = AgentClient("127.0.0.1", port, fp); c.pair(ag.code)
    yield ag, c, proj, log
    ag.end("test over"); srv.shutdown()


def test_wrong_fingerprint_refused_before_any_data(proj):
    ag, srv, fp, port, _ = start(proj)
    with pytest.raises(TransportError, match="identity mismatch"):
        AgentClient("127.0.0.1", port, "ab" * 32).pair(ag.code)
    assert ag.token is None and ag.attempts == 0  # the code never reached the agent
    srv.shutdown()


def test_pairing_code_rules(proj):
    ag, srv, fp, port, log = start(proj)
    c = AgentClient("127.0.0.1", port, fp)
    with pytest.raises(TargetError) as e:
        c.pair("12345678")
    assert e.value.status == 403
    c.pair(ag.code)
    with pytest.raises(TargetError) as e:
        AgentClient("127.0.0.1", port, fp).pair(ag.code)  # single use
    assert e.value.status == 409
    assert any("PAIRED" in l for l in log) and any("WRONG" in l for l in log)
    srv.shutdown()


def test_lockout_after_five_wrong_codes(proj):
    ag, srv, fp, port, _ = start(proj)
    c = AgentClient("127.0.0.1", port, fp)
    for _ in range(5):
        with pytest.raises(TargetError):
            c.pair("00000000")
    with pytest.raises(TargetError) as e:
        c.pair(ag.code)  # even the correct code is now refused
    assert e.value.status == 429
    assert ag.ended.wait(3)
    srv.shutdown()


def test_pairing_code_expires(proj):
    ag, srv, fp, port, _ = start(proj)
    ag.pair_expires = time.time() - 1
    with pytest.raises(TargetError) as e:
        AgentClient("127.0.0.1", port, fp).pair(ag.code)
    assert e.value.status == 410
    srv.shutdown()


def test_unauthenticated_requests_refused(proj):
    ag, srv, fp, port, _ = start(proj)
    with pytest.raises(TargetError) as e:
        AgentClient("127.0.0.1", port, fp).info()
    assert e.value.status == 401
    srv.shutdown()


def test_session_expiry_ends_agent(paired):
    ag, c, proj, _ = paired
    ag.expires_at = time.time() - 1
    with pytest.raises(TargetError) as e:
        c.info()
    assert e.value.status == 401 and ag.ended.is_set() and "expired" in ag.end_reason


def test_disconnect_is_immediate_and_stops_jobs(paired):
    ag, c, proj, _ = paired
    jid = c.exec_start([sys.executable, "-c", "import time;time.sleep(60)"], str(proj), 60)
    time.sleep(0.3)
    c.disconnect()
    assert ag.ended.is_set()
    assert ag.jobs.get(jid).cancelled
    with pytest.raises(TargetError):
        c.info()


def test_owner_can_end_session_locally(paired):
    ag, c, *_ = paired
    ag.end("disconnected by the computer's owner")
    with pytest.raises(TargetError) as e:
        c.info()
    assert e.value.status == 401


def test_fs_and_exec_over_tls(paired):
    ag, c, proj, _ = paired
    assert [e["name"] for e in c.fs_list(str(proj))] == ["a.txt"]
    c.fs_write(str(proj / "src/b.txt"), b"new")
    assert c.fs_read(str(proj / "src/b.txt")) == b"new"
    with pytest.raises(TargetError) as e:
        c.fs_read("/etc/passwd")
    assert e.value.status == 403
    jid = c.exec_start([sys.executable, "-c", "print(6*7)"], str(proj), 20)
    for _ in range(100):
        j = c.job(jid)
        if j["done"]:
            break
        time.sleep(0.05)
    assert j["output"].strip() == "42" and j["exit"] == 0
    c.fs_delete(str(proj / "src/b.txt"))
    assert not (proj / "src/b.txt").exists()


def test_cancel_over_tls(paired):
    ag, c, proj, _ = paired
    jid = c.exec_start([sys.executable, "-c", "import time;time.sleep(60)"], str(proj), 60)
    time.sleep(0.3)
    assert c.job_cancel(jid)["cancelled"]
    for _ in range(60):
        if c.job(jid)["done"]:
            break
        time.sleep(0.05)
    assert c.job(jid)["done"]


def test_grants_default_deny_over_tls(proj):
    ag, srv, fp, port, _ = start(proj, Grants())
    c = AgentClient("127.0.0.1", port, fp); c.pair(ag.code)
    assert c.info()["grants"] == []
    for f in (lambda: c.exec_start([sys.executable, "-V"], str(proj), 5), lambda: c.fs_write(str(proj / "x"), b"x"),
              lambda: c.claude_start("hi", str(proj), "default", [], 5)):
        with pytest.raises(TargetError) as e:
            f()
        assert e.value.status == 403
    assert c.fs_read(str(proj / "a.txt")) == b"hello"
    ag.end("done"); srv.shutdown()


def test_no_roots_means_no_filesystem(proj):
    ag, srv, fp, port, _ = start(proj, Grants(terminal=True), roots=False)
    c = AgentClient("127.0.0.1", port, fp); c.pair(ag.code)
    for f in (lambda: c.fs_list("."), lambda: c.exec_start([sys.executable, "-V"], None, 5)):
        with pytest.raises(TargetError) as e:
            f()
        assert e.value.status == 403
    ag.end("x"); srv.shutdown()


def test_agent_rejects_credential_fields(paired):
    ag, c, proj, _ = paired
    with pytest.raises(TargetError) as e:
        c._req("POST", "/claude", {"prompt": "x", "cwd": str(proj), "env": {"ANTHROPIC_API_KEY": "sk-personal"}})
    assert e.value.status == 400 and "unexpected fields" in str(e.value)
    import inspect
    assert "env" not in inspect.signature(AgentClient.claude_start).parameters


def test_tool_endpoint_obeys_repair_grant(proj):
    calls = []
    ps = lambda script, t: (calls.append(script) or (True, '{"ok":1}', ""))
    ag, srv, fp, port, _ = start(proj, Grants(), ps=ps)
    c = AgentClient("127.0.0.1", port, fp); c.pair(ag.code)
    r = c.run(get("cpu_ram"), {}, 10)
    assert r.ok and calls and "FromBase64String" in calls[0]
    with pytest.raises(TargetError) as e:
        c.run(get("flush_dns"), {}, 10)
    assert e.value.status == 403 and len(calls) == 1
    with pytest.raises(TargetError) as e:
        c._req("POST", "/tool", {"tool_id": "Invoke-Expression", "params": {}})
    assert e.value.status == 422
    ag.end("x"); srv.shutdown()
    ag2, srv2, fp2, port2, _ = start(proj, Grants(repair=True), ps=ps)
    c2 = AgentClient("127.0.0.1", port2, fp2); c2.pair(ag2.code)
    assert c2.run(get("flush_dns"), {}, 10).ok
    ag2.end("x"); srv2.shutdown()


def test_private_source_policy():
    import ipaddress
    assert cit_agent._private_ip(ipaddress.ip_address("192.168.1.9")) and cit_agent._private_ip(ipaddress.ip_address("100.64.1.1"))
    assert not cit_agent._private_ip(ipaddress.ip_address("8.8.8.8"))


def test_exec_log_redacts_secrets(paired):
    ag, c, proj, log = paired
    c.exec_start([sys.executable, "-c", "pass", "--password", "hunter2"], str(proj), 10)
    assert any("EXEC" in l for l in log) and not any("hunter2" in l for l in log)
