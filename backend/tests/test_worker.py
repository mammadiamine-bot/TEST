import base64
import importlib.util
import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.targetcore import TargetError
from app.transports.agent import AgentClient, WorkerClient
from app.transports.base import TransportError
from tests.test_tasks import HERE, install_fake

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cit_worker", ROOT / "worker" / "cit_worker.py")
cw = importlib.util.module_from_spec(spec); sys.modules["cit_worker"] = cw; spec.loader.exec_module(cw)


@pytest.fixture
def fakebin(tmp_path, monkeypatch):
    install_fake(tmp_path / "bin", "claude", HERE / "fake_claude.py")
    monkeypatch.setenv("PATH", f"{tmp_path/'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    return tmp_path / "claude.log"


def boot(data: Path, port=0, **kw):
    w = cw.Worker(data, cw.FileKeyProtector(data), say=lambda *_: None, tick=0.05, **kw)
    srv, fp = cw.serve(w, "127.0.0.1", port)
    return w, srv, fp


def enroll(w, srv, fp, name="laptop") -> WorkerClient:
    c = WorkerClient("127.0.0.1", srv.server_address[1], fp)
    c.enroll(w.new_enroll_code(), name)
    return c


@pytest.fixture
def wk(tmp_path, fakebin):
    w, srv, fp = boot(tmp_path / "wdata")
    c = enroll(w, srv, fp)
    yield type("W", (), dict(w=w, srv=srv, fp=fp, c=c, data=tmp_path / "wdata", log=fakebin, tmp=tmp_path))
    w.stop.set(); w.cancel_everything(); srv.shutdown()


def mkclient(wk, cid, name=None, systems=None):
    return wk.c.call("POST", "/clients", {"id": cid, "name": name or cid.title(), "systems": systems or []})


def mkproj(wk, cid, name="app", **kw):
    return wk.c.call("POST", f"/clients/{cid}/projects", {"name": name, **kw})


def put(wk, cid, rel, text):
    ws = wk.w.ws(cid)
    wk.c.call("PUT", f"/clients/{cid}/fs/write", {"path": str(ws / rel), "content_b64": base64.b64encode(text.encode()).decode()})


def task(wk, cid, proj, kind, **kw):
    return wk.c.call("POST", "/tasks", {"client_id": cid, "project": proj, "kind": kind, **kw})


def wait(wk, tid, states, secs=15):
    end = time.time() + secs
    while time.time() < end:
        t = wk.c.call("GET", f"/tasks/{tid}")
        if t["status"] in states:
            return t
        time.sleep(0.05)
    raise AssertionError(f"task stuck in {t['status']}: {[e['msg'] for e in t['events']][-5:]}")


# ------------------------------------------------------------------ enrollment & identity
def test_enrollment_rules(tmp_path):
    w, srv, fp = boot(tmp_path / "w")
    port = srv.server_address[1]
    c = WorkerClient("127.0.0.1", port, fp)
    with pytest.raises(TargetError) as e:
        c.enroll("12345678", "x")
    assert e.value.status == 410  # no code was issued on the machine
    code = w.new_enroll_code()
    with pytest.raises(TargetError) as e:
        c.enroll("00000000", "x")
    assert e.value.status == 403
    d = c.enroll(code, "laptop", days=30)
    with pytest.raises(TargetError):
        WorkerClient("127.0.0.1", port, fp).enroll(code, "again")  # single use
    assert c.call("GET", "/status")["worker_id"] == w.id and d["expires"]
    with pytest.raises(TargetError) as e:
        WorkerClient("127.0.0.1", port, fp).call("GET", "/status")
    assert e.value.status == 401
    with pytest.raises(TransportError, match="identity mismatch"):
        WorkerClient("127.0.0.1", port, "ab" * 32).enroll(w.new_enroll_code(), "attacker")
    w.stop.set(); srv.shutdown()


def test_enroll_attempt_limit(tmp_path):
    w, srv, fp = boot(tmp_path / "w")
    code = w.new_enroll_code()
    c = WorkerClient("127.0.0.1", srv.server_address[1], fp)
    for _ in range(5):
        with pytest.raises(TargetError):
            c.enroll("00000000", "x")
    with pytest.raises(TargetError):
        c.enroll(code, "x")  # code cancelled after 5 wrong attempts
    w.stop.set(); srv.shutdown()


def test_identity_and_credentials_survive_restart(tmp_path, fakebin):
    w, srv, fp = boot(tmp_path / "w"); port = srv.server_address[1]
    c = enroll(w, srv, fp)
    srv.shutdown(); w.stop.set(); srv.server_close()
    w2, srv2, fp2 = boot(tmp_path / "w", port)
    assert fp2 == fp and w2.id == w.id  # same identity -> the pinned fingerprint keeps working
    assert c.call("GET", "/status")["worker_id"] == w.id  # same token still valid
    assert not (tmp_path / "w" / "key.enc").read_bytes().startswith(b"-----BEGIN")  # private key is stored protected
    w2.stop.set(); srv2.shutdown()


def test_credential_revocation_is_immediate(wk):
    cid = wk.c.call("GET", "/credentials")[0]["id"]
    c2 = enroll(wk.w, wk.srv, wk.fp, "second")
    wk.c.call("DELETE", f"/credentials/{cid}")
    with pytest.raises(TargetError) as e:
        wk.c.call("GET", "/status")
    assert e.value.status == 401 and c2.call("GET", "/status")
    wk.w.revoke_all()
    with pytest.raises(TargetError):
        c2.call("GET", "/status")


# ------------------------------------------------------------------ client isolation
def test_cross_client_files_and_secrets_isolated(wk):
    mkclient(wk, "acme"); mkclient(wk, "globex"); mkproj(wk, "acme"); mkproj(wk, "globex")
    put(wk, "acme", "app/secret.txt", "acme-data")
    a_path = str(wk.w.ws("acme") / "app/secret.txt")
    with pytest.raises(TargetError) as e:
        wk.c.call("GET", f"/clients/globex/fs/read?path={a_path}")
    assert e.value.status == 403
    with pytest.raises(TargetError):
        wk.c.call("GET", f"/clients/globex/fs/list?path={wk.w.ws('acme')}")
    with pytest.raises(TargetError):
        wk.c.call("GET", f"/clients/globex/fs/read?path=../acme/app/secret.txt")
    wk.c.call("PUT", "/clients/acme/secrets/DB_PASSWORD", {"value": "hunter2"})
    assert wk.c.call("GET", "/clients/acme/secrets") == ["DB_PASSWORD"] and wk.c.call("GET", "/clients/globex/secrets") == []
    assert "hunter2" not in json.dumps(wk.c.call("GET", "/audit")) and "hunter2" not in json.dumps(wk.c.call("GET", "/clients"))
    assert not any("hunter2" in p.read_text(errors="ignore") for p in wk.data.rglob("*") if p.is_file() and p.suffix in (".enc",))  # encrypted at rest


def test_claude_jobs_get_only_their_own_client_credentials(wk, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "OWNERS-PERSONAL-KEY")
    for cid in ("acme", "globex", "initech"):
        mkclient(wk, cid); mkproj(wk, cid)
    wk.c.call("PUT", "/clients/acme/secrets/ANTHROPIC_API_KEY", {"value": "key-for-acme"})
    wk.c.call("PUT", "/clients/globex/secrets/ANTHROPIC_API_KEY", {"value": "key-for-globex"})
    ids = {}
    for cid in ("acme", "globex", "initech"):
        t = task(wk, cid, "app", "claude", instruction="hello")
        wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"}); ids[cid] = t["id"]
    for cid, tid in ids.items():
        wait(wk, tid, {"completed_unverified", "completed", "failed"})
    log = [json.loads(l) for l in wk.log.read_text().splitlines()]
    by_cwd = {Path(l["cwd"]).parent.name: l for l in log}
    assert by_cwd["acme"]["key"] == "key-for-acme" and by_cwd["globex"]["key"] == "key-for-globex"
    assert by_cwd["initech"]["key"] is None  # the owner's personal key never reaches a client's job
    assert len({l["config"] for l in log}) == 3 and all("/.claude-home" in l["config"] for l in log)


def test_terminal_is_confined_and_scrubbed(wk, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "t0ken")
    mkclient(wk, "acme"); mkclient(wk, "globex")
    ws = str(wk.w.ws("acme"))
    jid = wk.c.call("POST", "/clients/acme/exec", {"argv": [sys.executable, "-c", "import os;print(os.getcwd(), os.environ.get('SOME_API_TOKEN'))"]})["job"]
    for _ in range(100):
        j = wk.c.call("GET", f"/clients/acme/jobs/{jid}")
        if j["done"]:
            break
        time.sleep(0.05)
    assert j["output"].strip() == f"{os.path.realpath(ws)} None"
    with pytest.raises(TargetError) as e:
        wk.c.call("POST", "/clients/globex/exec", {"argv": [sys.executable, "-V"], "cwd": ws})
    assert e.value.status == 403
    with pytest.raises(TargetError):
        wk.c.call("GET", f"/clients/acme/jobs/{jid}".replace("acme", "globex"))  # job ids are per client


def test_unknown_fields_and_bad_names_rejected(wk):
    with pytest.raises(TargetError):
        wk.c.call("POST", "/clients", {"id": "x", "name": "x", "env": {"A": "b"}})
    for bad in ("../x", "UPPER", "a b", "a"):
        with pytest.raises(TargetError):
            wk.c.call("POST", "/clients", {"id": bad, "name": "x"})
    mkclient(wk, "acme")
    with pytest.raises(TargetError):
        mkproj(wk, "acme", git_url="--upload-pack=evil")
    with pytest.raises(TargetError):
        wk.c.call("PUT", "/clients/acme/secrets/lowercase", {"value": "v"})


# ------------------------------------------------------------------ task lifecycle
def test_task_needs_approval_then_runs_with_checkpoint_and_verification(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme", test_cmd=[sys.executable, "-c", "import sys;sys.exit(0 if 'DONE' in open('a.py').read() else 1)"])
    put(wk, "acme", "app/a.py", "x = 1  # TODO\n")
    t = task(wk, "acme", "app", "claude", instruction="FAKE:replace TODO=>DONE in a.py")
    assert t["status"] == "awaiting_approval"
    time.sleep(0.5)
    assert (wk.w.ws("acme") / "app/a.py").read_text().endswith("TODO\n")  # nothing runs before approval
    wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    d = wait(wk, t["id"], {"completed", "failed", "completed_unverified"})
    assert d["status"] == "completed" and "tests" in d["data"]["result"]["detail"]
    assert d["data"]["session_id"].startswith("sess-") and any("tool Edit" in e["msg"] for e in d["events"])
    cps = wk.c.call("GET", f"/tasks/{t['id']}/checkpoints")
    assert len(cps) == 1
    wk.c.call("POST", f"/checkpoints/{cps[0]['id']}/restore", {})
    assert (wk.w.ws("acme") / "app/a.py").read_text().endswith("TODO\n")  # recovery to the pre-task state
    assert [e["seq"] for e in d["events"]] == sorted(e["seq"] for e in d["events"])
    assert wk.c.call("GET", f"/tasks/{t['id']}?since={d['last_seq']}")["events"] == []


def test_failed_tests_mean_failed_task_and_no_tests_means_unverified(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme", "bad", test_cmd=[sys.executable, "-c", "import sys;sys.exit(1)"]); mkproj(wk, "acme", "none")
    t1 = task(wk, "acme", "bad", "claude", instruction="hello"); wk.c.call("POST", f"/tasks/{t1['id']}/approve", {"approver": "me"})
    t2 = task(wk, "acme", "none", "claude", instruction="hello"); wk.c.call("POST", f"/tasks/{t2['id']}/approve", {"approver": "me"})
    assert wait(wk, t1["id"], {"failed", "completed"})["status"] == "failed"
    d = wait(wk, t2["id"], {"completed_unverified", "completed", "failed"})
    assert d["status"] == "completed_unverified" and "NOT verified" in d["data"]["result"]["detail"]


def test_production_deploy_needs_typed_confirmation_and_is_checkpointed(wk):
    mkclient(wk, "acme", "Acme Corp"); mkproj(wk, "acme", deploy_cmd=[sys.executable, "-c", "open('deployed.txt','w').write('ok')"])
    with pytest.raises(TargetError) as e:
        task(wk, "acme", "app", "command", argv=None)
    t = task(wk, "acme", "app", "deploy")
    assert t["data"]["production"] and t["data"]["destructive"]
    for body in ({"approver": "me"}, {"approver": "me", "confirm_text": "acme/app"}):
        with pytest.raises(TargetError) as e:
            wk.c.call("POST", f"/tasks/{t['id']}/approve", body)
        assert e.value.status == 428
    wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me", "confirm_text": "Acme Corp/app"})
    d = wait(wk, t["id"], {"completed", "failed", "completed_unverified"})
    assert d["status"] == "completed_unverified" and (wk.w.ws("acme") / "app/deployed.txt").read_text() == "ok"
    assert wk.c.call("GET", f"/tasks/{t['id']}/checkpoints")


def test_cancel_running_task_kills_the_process(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme")
    t = task(wk, "acme", "app", "claude", instruction="FAKE:sleep"); wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    wait(wk, t["id"], {"running"})
    for _ in range(100):
        if any("working" in e["msg"] for e in wk.c.call("GET", f"/tasks/{t['id']}")["events"]):
            break
        time.sleep(0.05)
    wk.c.call("POST", f"/tasks/{t['id']}/cancel", {})
    d = wait(wk, t["id"], {"cancelled"})
    assert d["status"] == "cancelled"
    jm = wk.w.jms["acme"]
    assert all(j.exit is not None for j in jm.jobs.values())


def test_tasks_continue_when_the_controller_disconnects(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme")
    t = task(wk, "acme", "app", "command", argv=[sys.executable, "-c", "import time;time.sleep(1.2);open('done.txt','w').write('x')"])
    wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    tid, token = t["id"], wk.c.token
    wait(wk, tid, {"running"})
    del wk.c  # the laptop vanishes
    time.sleep(2.0)
    fresh = WorkerClient("127.0.0.1", wk.srv.server_address[1], wk.fp, token)  # reconnect later with the stored credential
    d = fresh.call("GET", f"/tasks/{tid}")
    assert d["status"] == "completed" and (wk.w.ws("acme") / "app/done.txt").exists()
    assert any("started" in e["msg"] for e in d["events"])  # full history is there to replay


def test_pause_and_resume_claude_session_safely(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme")
    t = task(wk, "acme", "app", "claude", instruction="FAKE:sleep"); wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    for _ in range(100):
        if wk.c.call("GET", f"/tasks/{t['id']}")["data"]["session_id"]:
            break
        time.sleep(0.05)
    wk.c.call("POST", f"/tasks/{t['id']}/pause", {})
    assert wait(wk, t["id"], {"paused"})["status"] == "paused"
    wk.c.call("POST", f"/tasks/{t['id']}/resume", {})
    d = wait(wk, t["id"], {"completed_unverified", "completed", "failed"})
    assert d["status"] == "completed_unverified"
    runs = [json.loads(l) for l in wk.log.read_text().splitlines()]
    assert "--resume" in runs[-1]["argv"] and runs[-1]["argv"][runs[-1]["argv"].index("--resume") + 1].startswith("sess-")
    assert len(wk.c.call("GET", f"/tasks/{t['id']}/checkpoints")) == 1  # resuming does not re-checkpoint over the user's work


def test_one_running_task_per_project_and_queue_order(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme")
    ids = []
    for i in range(3):
        t = task(wk, "acme", "app", "command", argv=[sys.executable, "-c", f"import time;time.sleep(0.4);open('o{i}','w').write('x')"])
        wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"}); ids.append(t["id"])
    time.sleep(0.2)
    st = wk.c.call("GET", "/status")
    assert st["active_tasks"] == 1 and st["queue"].get("queued") == 2
    for i in ids:
        assert wait(wk, i, {"completed_unverified", "completed"}, 20)["status"].startswith("completed")


# ------------------------------------------------------------------ crash recovery
def _crash(wk, status, **data):
    """Simulate a hard stop: write the task as it would look mid-run, then boot a fresh worker on the same data dir."""
    return None


def test_recovery_never_reruns_destructive_work(tmp_path, fakebin):
    d = tmp_path / "w"
    w, srv, fp = boot(d); c = enroll(w, srv, fp)
    c.call("POST", "/clients", {"id": "acme", "name": "Acme"}); c.call("POST", "/clients/acme/projects", {"name": "app"})
    marker = d / "workspaces/acme/app/count.txt"
    t_cmd = c.call("POST", "/tasks", {"client_id": "acme", "project": "app", "kind": "command", "argv": [sys.executable, "-c", "open('count.txt','a').write('x');import time;time.sleep(30)"]})
    c.call("POST", f"/tasks/{t_cmd['id']}/approve", {"approver": "me"})
    t_cl = c.call("POST", "/tasks", {"client_id": "acme", "project": "other-never-run", "kind": "claude", "instruction": "x"}) if False else None
    for _ in range(100):
        if marker.exists() and c.call("GET", f"/tasks/{t_cmd['id']}")["status"] == "running":
            break
        time.sleep(0.05)
    t = w.task(t_cmd["id"]); assert t["data"]["attempt"] and t["data"]["proc"]
    # --- crash: the old worker object is abandoned WITHOUT any cleanup (its job process keeps running, like after kill -9)
    srv.shutdown(); srv.server_close(); w.stop.set()
    w2 = cw.Worker(d, cw.FileKeyProtector(d), say=lambda *_: None, tick=0.05)
    r = w2.task(t_cmd["id"])
    assert r["status"] == "needs_review" and "NOT re-run" in r["events"][-1]["msg"]
    with pytest.raises(TargetError) as e:
        w2.resume(t_cmd["id"], "me")
    assert e.value.status == 409
    w2.start(); time.sleep(0.6)
    assert marker.read_text() == "x"  # the destructive command was NOT executed again
    r2 = w2.retry(t_cmd["id"], "me")
    assert r2["status"] == "awaiting_approval"  # only a brand-new approval can run it again
    import psutil
    pid = t["data"]["proc"]["pid"]
    for _ in range(40):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE  # orphaned job was stopped
    w2.stop.set()


def test_recovery_auto_resume_is_opt_in_and_only_for_claude_sessions(tmp_path, fakebin):
    d = tmp_path / "w"
    w = cw.Worker(d, cw.FileKeyProtector(d), say=lambda *_: None, tick=0.05)
    w.create_client("acme", "Acme", [], None, "t"); w.create_project("acme", "app", None, None, None, None, [], "t")
    def mk(kind, **kw):
        t = w.create_task("acme", "app", kind, "x" if kind == "claude" else "", ["echo", "x"] if kind == "command" else None, False, False, "t")
        t["data"].update(kw); t["status"] = "running"; w._save(t); return t["id"]
    a = mk("claude", session_id="sess-0123456789abcdef"); b = mk("claude"); c = mk("command", attempt=time.time())
    w.stop.set()
    w1 = cw.Worker(d, cw.FileKeyProtector(d), say=lambda *_: None)
    assert [w1.task(i)["status"] for i in (a, b, c)] == ["interrupted", "interrupted", "needs_review"]
    w1.stop.set()
    for i in (a, b, c):
        t = w1.task(i); t["status"] = "running"; w1._save(t)
    w2 = cw.Worker(d, cw.FileKeyProtector(d), say=lambda *_: None, auto_resume=True)
    assert [w2.task(i)["status"] for i in (a, b, c)] == ["queued", "interrupted", "needs_review"]
    assert w2.task(a)["data"]["resume"] is True
    w2.stop.set()


def test_real_process_kill_and_restart(tmp_path, fakebin):
    """Real subprocess, SIGKILL, restart on the same data dir: identity, credential and task history survive."""
    d, port = tmp_path / "w", 18777
    env = {**os.environ}
    def start():
        return subprocess.Popen([sys.executable, str(ROOT / "worker/cit_worker.py"), "--data-dir", str(d), "--dev-insecure-store", "run", "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    d.mkdir()
    code = subprocess.run([sys.executable, str(ROOT / "worker/cit_worker.py"), "--data-dir", str(d), "--dev-insecure-store", "enroll"], capture_output=True, text=True).stdout.split(":")[-1].strip()
    p = start(); out = ""
    for _ in range(100):
        line = p.stdout.readline(); out += line
        if "Fingerprint:" in line:
            break
    fp = "".join(line.split(":")[1].split()).lower()
    c = WorkerClient("127.0.0.1", port, fp); c.enroll(code, "laptop")
    c.call("POST", "/clients", {"id": "acme", "name": "Acme"}); c.call("POST", "/clients/acme/projects", {"name": "app"})
    t = c.call("POST", "/tasks", {"client_id": "acme", "project": "app", "kind": "command", "argv": [sys.executable, "-c", "import time;time.sleep(60)"]})
    c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    for _ in range(100):
        if c.call("GET", f"/tasks/{t['id']}")["status"] == "running":
            break
        time.sleep(0.05)
    p.kill(); p.wait()
    with pytest.raises(TransportError):
        c.call("GET", "/status")  # offline
    p2 = start()
    for _ in range(100):
        if "Fingerprint:" in p2.stdout.readline():
            break
    st = c.call("GET", "/status")  # same fingerprint (pinned), same token
    assert st["worker_id"] and st["secret_store"].startswith("NOT OS-backed")
    r = c.call("GET", f"/tasks/{t['id']}")
    assert r["status"] == "needs_review" and "NOT re-run" in r["events"][-1]["msg"]
    p2.kill(); p2.wait()


# ------------------------------------------------------------------ lock / authorization / emergency
def test_emergency_stop_locks_everything(wk):
    mkclient(wk, "acme"); mkproj(wk, "acme")
    t = task(wk, "acme", "app", "claude", instruction="FAKE:sleep"); wk.c.call("POST", f"/tasks/{t['id']}/approve", {"approver": "me"})
    wait(wk, t["id"], {"running"})
    wk.c.call("POST", "/emergency-stop", {})
    d = wait(wk, t["id"], {"cancelled", "failed"})
    assert d["status"] in ("cancelled", "failed")
    with pytest.raises(TargetError) as e:
        wk.c.call("GET", "/clients")
    assert e.value.status == 423
    with pytest.raises(TargetError) as e:
        task(wk, "acme", "app", "claude", instruction="new work")
    assert e.value.status == 423
    assert wk.c.call("GET", "/tasks")  # history stays readable
    assert wk.c.call("GET", "/status")["locked"]
    with pytest.raises(TargetError):
        wk.c.call("POST", "/unlock", {})  # needs explicit confirm
    wk.c.call("POST", "/unlock", {"confirm": True})
    assert wk.c.call("GET", "/clients") is not None


def test_stop_file_and_authorization_expiry_lock_the_worker(wk):
    (wk.data / "STOP").write_text("")
    for _ in range(60):
        if wk.w.locked():
            break
        time.sleep(0.05)
    assert "STOP file" in wk.w.locked()
    (wk.data / "STOP").unlink(); wk.w.unlock("me")
    wk.w.store.setkv("authorized_until", time.time() - 1)
    for _ in range(60):
        if wk.w.locked():
            break
        time.sleep(0.05)
    assert "expired" in wk.w.locked()
    with pytest.raises(TargetError) as e:
        wk.w.unlock("me")
    assert e.value.status == 403  # only extending the window ON THE MACHINE re-enables it
    wk.w.authorize(1); wk.w.unlock("local-owner")
    assert not wk.w.locked()


# ------------------------------------------------------------------ network policy
def test_no_scanning_only_approved_systems(wk):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5); port = s.getsockname()[1]
    mkclient(wk, "acme", systems=[{"host": "127.0.0.1", "port": port}])
    assert wk.c.call("POST", "/clients/acme/system-check", {"host": "127.0.0.1", "port": port})["reachable"] is True
    for host, p in (("127.0.0.1", port + 1), ("192.168.1.10", 22), ("10.0.0.1", port)):
        with pytest.raises(TargetError) as e:
            wk.c.call("POST", "/clients/acme/system-check", {"host": host, "port": p})
        assert e.value.status == 403
    assert any(a["action"] == "network.refused" for a in wk.c.call("GET", "/audit"))
    with pytest.raises(TargetError):
        mkclient(wk, "bad", systems=[{"host": "x; rm -rf", "port": 22}])


def test_firewall_script_blocks_lan_except_approved(wk):
    mkclient(wk, "acme", systems=[{"host": "192.168.1.50", "port": 22}, {"host": "git.example.com", "port": 443}])
    script = wk.c.call("GET", "/firewall-script?infra=192.168.1.1,192.168.1.2")["script"]
    line = next(l for l in script.splitlines() if l.startswith("New-NetFirewallRule"))
    nets = [ipaddress.ip_network(x) for x in line.split("-RemoteAddress ")[1].split(",")]
    blocked = lambda ip: any(ipaddress.ip_address(ip) in n for n in nets)
    assert not blocked("192.168.1.50") and not blocked("192.168.1.1") and not blocked("192.168.1.2")  # approved + gateway/DNS reachable
    assert blocked("192.168.1.51") and blocked("192.168.0.7") and blocked("10.1.2.3") and blocked("172.20.0.9") and blocked("169.254.3.3")
    assert not blocked("100.101.102.103") and not blocked("8.8.8.8")  # Tailscale + Internet untouched
    assert "Remove-NetFirewallRule" in script


# ------------------------------------------------------------------ removal / service
def test_wipe_requires_name_and_removes_everything(tmp_path, fakebin):
    d = tmp_path / "w"; w = cw.Worker(d, cw.FileKeyProtector(d), say=lambda *_: None)
    w.create_client("acme", "Acme", [], None, "t"); w.set_secret("acme", "ANTHROPIC_API_KEY", "sk-secret", "t")
    with pytest.raises(TargetError) as e:
        w.wipe("wrong")
    assert e.value.status == 428 and d.exists()
    r = w.wipe(w.name)
    assert not d.exists() and r["overwritten"] >= 2 and any("scheduled task" in s for s in r["remaining_steps"])


def test_service_install_is_explicit_visible_and_removable(tmp_path):
    cmds = cw.service_commands(r"C:\Program Files\CIT\cit-worker.exe", r"C:\ProgramData\ClaudeITWorker", "citworker", "192.168.1.105", 8777)
    ins = cmds["install"]
    assert ins[:4] == ["schtasks", "/Create", "/TN", "Claude IT Technician Worker"] and "LIMITED" in ins
    assert "ONLOGON" in ins and "/IT" in ins and "/RP" not in ins and "ONSTART" not in ins  # at log on, interactive-only, no stored password
    tr = ins[ins.index("/TR") + 1]  # --data-dir is a GLOBAL option: it must come before `run` or argparse rejects the task's command line
    assert tr.index("--data-dir") < tr.index(" run ") and "--bind 192.168.1.105" in tr and "--port 8777" in tr
    assert cmds["remove"][:3] == ["schtasks", "/Delete", "/TN"]
    assert cw.main(["--data-dir", str(tmp_path), "service", "install"]) == 2  # refuses without --user and --i-understand
    assert not (tmp_path / "nothing").exists()


def test_task_command_line_actually_parses(tmp_path):
    import argparse, shlex
    cmds = cw.service_commands("cit-worker.exe", str(tmp_path), "u", "127.0.0.1", 8777)
    tr = cmds["install"][cmds["install"].index("/TR") + 1]
    argv = shlex.split(tr.replace("\\", "/"))[1:]  # what the exe would receive
    p = argparse.ArgumentParser(); p.add_argument("--data-dir"); sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run"); r.add_argument("--bind"); r.add_argument("--port", type=int)
    assert p.parse_args(argv).cmd == "run"


def test_only_one_worker_per_data_dir(tmp_path):
    first = cw.single_instance(tmp_path)
    assert first is not None
    assert cw.single_instance(tmp_path) is None  # a second instance on the same data directory is refused
    first.close()
    again = cw.single_instance(tmp_path)
    assert again is not None
    again.close()


def test_server_port_is_not_shareable_on_windows(tmp_path):
    import os, socket
    if os.name != "nt":
        pytest.skip("Windows-only port semantics")
    w = cw.Worker(tmp_path, cw.FileKeyProtector(tmp_path), say=lambda *_: None, tick=0.1)
    srv, _ = cw.serve(w, "127.0.0.1", 0)
    try:
        port = srv.server_address[1]
        s2 = socket.socket(); s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            s2.bind(("127.0.0.1", port))
        s2.close()
    finally:
        srv.shutdown(); w.stop.set()


def test_secret_store_refuses_without_os_protection(tmp_path):
    prot = cw.make_protector(tmp_path, dev_store=False)
    assert prot.os_backed is False
    w = cw.Worker(tmp_path / "w", prot, say=lambda *_: None)
    w.create_client("acme", "Acme", [], None, "t")
    with pytest.raises(RuntimeError):
        w.set_secret("acme", "ANTHROPIC_API_KEY", "x", "t")  # never stored in clear


def test_resources_reported(wk):
    st = wk.c.call("GET", "/status")
    assert 0 <= st["cpu"] <= 100 and 0 <= st["ram"] <= 100 and 0 <= st["disk"] <= 100 and "queue" in st and "claude" in st["tools"]
