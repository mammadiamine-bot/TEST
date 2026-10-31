"""AI technician agent: real multi-step diagnosis, policy-gated repairs, verification, rollback, approvals, emergency stop, knowledge, Arabic."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.aitech import Ollama
from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core, CoreError
from tests.test_tasks import XorProtector


class Box:
    """A stateful fake Windows computer behind the Worker channel: repairs really change what the next measurement returns."""
    def __init__(self):
        self.n, self.jobs, self.executed = 0, {}, []
        self.fail, self.denied, self.down = set(), set(), False
        self.s = dict(total=8000, free=400, cpu=40, temp=2500, free_gb=60, dns_ok=True, dns_ms=40, spooler=True, chrome=3500, startup=6, reboot=False)

    def perf(self):
        s = self.s
        top = [{"Id": 11, "ProcessName": "chrome", "cpu_pct": 5, "mem_mb": s["chrome"]}] if s["chrome"] else []
        return {"host": "PC1", "cores": 4, "cpu_pct": s["cpu"], "mem_total_mb": s["total"], "mem_free_mb": s["free"], "disk_busy_pct": 4, "disk_queue": 0, "disk_ms": 5, "uptime_hours": 30, "proc_count": 150,
                "top_cpu": [{"Id": 11, "ProcessName": "chrome", "cpu_pct": 5, "mem_mb": s["chrome"]}], "top_mem": top + [{"Id": 12, "ProcessName": "code", "cpu_pct": 1, "mem_mb": 300}]}

    def run(self, script):
        s = self.s
        if "Start-Service" in script:
            if "start" in self.fail:
                return "Start-Service : cannot start", 1
            s["spooler"] = True; return "Running", 0
        if "Stop-Service" in script:
            s["spooler"] = False; return "Stopped", 0
        if "CloseMainWindow" in script:
            s["free"] += s["chrome"]; s["chrome"] = 0; return json.dumps({"stopped": 1, "forced": 0}), 0
        if "Clear-DnsClientCache" in script:
            if "dns" in self.fail:
                return "boom", 1
            s["dns_ok"], s["dns_ms"] = True, 30; return "dns cache cleared", 0
        if "freed_mb" in script:
            freed = s["temp"]; s["temp"] = 100; return json.dumps({"candidates": 90, "freed_mb": freed - 100}), 0
        if "Restart-Service" in script:
            return "Running", 0
        if "shutdown.exe /r" in script:
            return "", 0
        marker_data = [("top_cpu", lambda: self.perf()), ("Win32_StartupCommand", lambda: {"count": s["startup"], "items": [{"Name": "A"}]}),
                       ("Get-MpComputerStatus", lambda: {"reboot_pending": s["reboot"], "defender": {"present": True, "service": True, "realtime": True, "signature_age_days": 0}, "bad_devices": [], "disks": [], "firewall": [{"Name": "Public", "Enabled": 1}], "crashes": [], "update_errors": []}),
                       ("Test-Connection", lambda: {"gateway": "192.168.1.1", "gw_sent": 4, "gw_recv": 4, "gw_ms": 5, "net_recv": 4, "net_ms": 12, "dns_ms": s["dns_ms"], "dns_resolves": s["dns_ok"], "wifi_signal": 80, "proxy_enabled": False, "adapters": [], "firewall": [], "events": []}),
                       ("recycle_hint", lambda: {"temp_mb": s["temp"], "downloads_mb": 10}), ("Win32_LogicalDisk", lambda: {"host": "PC1", "ram_gb": 8, "disks": [{"DeviceID": "C:", "size_gb": 200, "free_gb": s["free_gb"]}]}),
                       ("Get-Service | Select-Object Name", lambda: [{"Name": "Spooler", "DisplayName": "Print Spooler", "Status": 4 if s["spooler"] else 1, "StartType": 2}]),
                       ("Test-NetConnection", lambda: {"internet": True, "dns_resolves": s["dns_ok"], "dns": [], "gateway": []}), ("Get-HotFix", lambda: {"reboot_pending": s["reboot"], "wuauserv": "Running", "last_hotfixes": []}),
                       ("WorkingSet64", lambda: self.perf()["top_mem"]), ("Get-WinEvent", lambda: [])]
        for key, fn in marker_data:
            if key in script:
                if any(d in self.denied for d in ("*",)) or ("Get-MpComputerStatus" in script and "health" in self.denied):
                    return "Access is denied", 1
                return json.dumps(fn()), 0
        return "{}", 0

    def __call__(self, wid, method, path, body=None, actor="x"):
        if path == "/clients":
            raise CoreError("exists", 409)
        if self.down:
            raise CoreError("cannot reach the computer", 502)
        if path.endswith("/exec"):
            self.n += 1; self.jobs[f"j{self.n}"] = body["argv"][-1]; self.executed.append(body["argv"][-1])
            return {"job": f"j{self.n}"}
        if path.endswith("/cancel"):
            return {}
        out, code = self.run(self.jobs[path.split("?")[0].split("/")[-1]])
        return {"output": out, "next": 1, "done": True, "exit": code}


class OllamaStub:
    def __init__(self, models=("qwen2.5:1.5b",), reply="The computer is short of memory because Chrome uses most of it."):
        self.models, self.reply, self.seen = list(models), reply, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, o):
                b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self): self._send({"models": [{"name": m, "size": 1_000_000_000, "details": {"parameter_size": "1.5B", "family": "qwen2"}} for m in outer.models]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); outer.seen.append(body)
                content = '{"tools": ["a"]}' if body.get("format") == "json" else outer.reply
                self._send({"message": {"content": content}, "done": True, "prompt_eval_count": 50, "eval_count": 10})
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self): self.srv.shutdown()


@pytest.fixture
def w(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    box = Box(); app.state.ops.call, app.state.ops.retry_delay = box, 0.0
    app.state.chat.call = box
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]
    b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]
    wid = "wst_a00000000"
    core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES(?,?,?,?,?,?,?)", (wid, "a-pc", "127.0.0.1:1", "ab" * 32, "enrolled", time.time(), a))
    stubs = []

    def use_ollama(**kw):
        st = OllamaStub(**kw); stubs.append(st)
        app.state.ai.ollama = Ollama(st.url, ["qwen2.5:1.5b"]); app.state.agent.ollama = app.state.ai.ollama; app.state.chat.ollama = app.state.ai.ollama; app.state.agent.models_cache.clear()
        return st

    def mint(role, orgs):
        r = owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()
        return TestClient(app, headers={"Authorization": f"Bearer {r['token']}"})
    app.state.ai.ollama = app.state.agent.ollama = Ollama("http://127.0.0.1:9", ["x"])   # default: no model reachable
    app.state.chat.ollama = app.state.ai.ollama
    local = {"calls": []}

    def local_run(script, timeout=120):
        local["calls"].append(script); out, code = box.run(script); return code, out
    app.state.agent.local_run = local_run
    yield type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, wid=wid, box=box, use_ollama=use_ollama, mint=mint, local=local, agent=app.state.agent))
    for st in stubs:
        st.stop()


def set_level(w, level, per_hour=6):
    r = w.owner.put(f"/orgs/{w.a}/agent/policy", json={"level": level, "max_auto_per_hour": per_hour}); assert r.status_code == 200, r.text


def run(w, text, client=None, wait=("completed", "awaiting_approval", "cancelled", "failed"), target=None, kind="worker", lang="en", timeout=90):
    c = client or w.owner
    r = c.post(f"/orgs/{w.a}/agent/sessions", json={"request": text, "target_kind": kind, "target": target or w.wid, "lang": lang})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    return settle(w, sid, c, wait, timeout)


def settle(w, sid, c=None, wait=("completed", "awaiting_approval", "cancelled", "failed"), timeout=90):
    c = c or w.owner
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/orgs/{w.a}/agent/sessions/{sid}").json()
        if j["status"] in wait:
            if j["status"] == "failed":
                print("SESSION FAILED:", [e for e in j["events"] if e["kind"] == "error"])
            return j
        time.sleep(0.2)
    raise AssertionError("session did not settle: " + j["status"])


def kinds(j):
    return [e["kind"] for e in j["events"]]


def tools(j):
    return [e["title"] for e in j["events"] if e["kind"] == "tool_end"]


SLOW = "My PC is slow. Find the cause and fix what is safe to fix."


# ---------------------------------------------------------------------------------------------------------------------------------------
def test_slow_pc_collects_real_metrics_ranks_causes_and_changes_nothing_at_the_default_level(w):
    assert w.owner.get(f"/orgs/{w.a}/agent/policy").json()["level"] == 1          # default: diagnose and propose only
    j = run(w, SLOW)
    assert j["status"] == "completed" and j["outcome"] == "problem_detected" and j["intent"] == "slow"
    assert {"perf.snapshot", "inventory.system", "startup.apps", "health.windows", "disk.usage"} <= set(tools(j))   # several real checks, not one generic one
    rep = j["report"]
    assert rep["causes"][0]["code"] == "mem_pressure" and rep["causes"][0]["confidence"] >= 0.8
    assert rep["before"]["mem_used_pct"] == 95 and rep["before"]["temp_mb"] == 2500
    plan = {p["workflow"]: p for p in rep["repair_plan"]}
    assert plan["temp_cleanup"]["decision"] == "propose" and plan["app_close"]["decision"] == "propose"            # proposals only at level 1
    assert j["actions"] == [] and not any("freed_mb" in c or "CloseMainWindow" in c for c in w.box.executed)        # not a single change was made
    assert "Likely causes" in j["answer"] and "Memory is almost exhausted" in j["answer"]


def test_level_two_runs_only_the_safe_allowlisted_repair_and_verifies_before_and_after(w):
    set_level(w, 2)
    j = run(w, SLOW)
    assert j["status"] == "completed"
    acts = {a["workflow"]: a for a in j["actions"]}
    assert set(acts) == {"temp_cleanup"} and acts["temp_cleanup"]["auto"] == 1 and acts["temp_cleanup"]["status"] == "ok"      # the process kill needs approval, so it did NOT run
    assert acts["temp_cleanup"]["verify"]["result"] == "pass" and "temp_mb" in acts["temp_cleanup"]["verify"]["why"]
    assert not any("CloseMainWindow" in c for c in w.box.executed)
    ba = {b["metric"]: b for b in j["report"]["before_after"]}
    assert ba["temp_mb"]["before"] == 2500 and ba["temp_mb"]["after"] == 100 and ba["temp_mb"]["verdict"] == "improved"
    assert j["resolution"] == "partially_resolved"       # the memory cause was not fixed: honest
    assert any(p["workflow"] == "app_close" and p["decision"] == "propose" for p in j["report"]["repair_plan"])
    assert w.db.one("SELECT COUNT(*) AS n FROM audit WHERE action='agent.action_created'")["n"] == 1


def test_level_three_requests_approval_four_eyes_then_verifies_the_outcome(w):
    set_level(w, 3)
    tech, admin = w.mint("technician", [w.a]), w.mint("administrator", [w.a])
    j = run(w, SLOW, client=tech)
    assert j["status"] == "awaiting_approval" and j["outcome"] == "problem_detected"
    pend = {a["workflow"]: a for a in j["actions"] if a["status"] == "pending_approval"}
    assert "app_close" in pend and not any("CloseMainWindow" in c for c in w.box.executed)                          # nothing ran yet
    aid = pend["app_close"]["id"]
    assert tech.post(f"/orgs/{w.a}/agent/actions/{aid}/approve", json={}).status_code == 403                         # the requester cannot approve their own repair
    assert admin.post(f"/orgs/{w.a}/agent/actions/{aid}/approve", json={}).status_code == 200
    j = settle(w, j["id"], wait=("completed",))
    a = next(x for x in j["actions"] if x["workflow"] == "app_close")
    assert a["status"] == "ok" and a["verify"]["result"] == "pass" and a["approved_by"]
    assert j["report"]["after"]["mem_used_pct"] < 60
    assert any(c for c in w.box.executed if "CloseMainWindow" in c)


def test_denied_repair_is_not_run_and_not_retried_forever(w):
    set_level(w, 3)
    admin = w.mint("administrator", [w.a])
    for i in range(2):
        j = run(w, SLOW, client=w.mint("technician", [w.a]))
        aid = next(a["id"] for a in j["actions"] if a["workflow"] == "app_close" and a["status"] == "pending_approval")
        assert admin.post(f"/orgs/{w.a}/agent/actions/{aid}/reject").status_code == 200
        j = settle(w, j["id"], wait=("completed",))
        assert next(a for a in j["actions"] if a["id"] == aid)["status"] == "rejected"
    assert not any("CloseMainWindow" in c for c in w.box.executed)
    j = run(w, SLOW)
    blocked = next(p for p in j["report"]["repair_plan"] if p["workflow"] == "app_close")
    assert blocked["decision"] == "blocked" and "not repeated" in blocked["reason"]            # budget: a refused repair is not asked again and again


def test_device_unreachable_is_reported_as_unreachable_never_as_healthy(w):
    w.box.down = True
    j = run(w, SLOW)
    assert j["outcome"] == "unreachable" and j["report"]["causes"] == []
    assert "Device unreachable" in j["answer"] and "Healthy" not in j["answer"]


def test_failed_checks_never_become_no_problems_found(w):
    # health check fails (generic error) while everything else is fine -> inconclusive, and the answer says what did not complete
    orig = w.box.run
    w.box.run = lambda sc: ("boom", 1) if "Get-MpComputerStatus" in sc else orig(sc)
    w.box.s.update(free=7000, chrome=300, temp=100)
    j = run(w, "check my computer health")
    assert j["outcome"] == "inconclusive" and "NOT a clean bill of health" in j["answer"] and "health.windows" in j["answer"]
    w.box.run = lambda sc: ("boom", 1)
    j = run(w, "check my computer health")
    assert j["outcome"] == "check_failed"
    w.box.run = lambda sc: ("Access is denied", 1)
    j = run(w, "check my computer health")
    assert j["outcome"] == "permission_required"


def test_healthy_requires_every_check_to_pass_and_a_symptom_not_reproduced_is_inconclusive(w):
    w.box.s.update(free=7000, chrome=300, temp=100, cpu=10)
    j = run(w, "give the computer a general health check")
    assert j["outcome"] == "healthy" and j["report"]["causes"] == []
    j = run(w, "my computer is slow")
    assert j["outcome"] == "inconclusive" and "Measured values" in j["answer"] and "mem_used_pct" in j["answer"]    # normal numbers do not prove the slowness away


def test_arabic_request_gets_arabic_report_without_english_findings(w):
    j = run(w, "الكمبيوتر بطيء جداً، ابحث عن السبب", lang="ar")
    assert j["lang"] == "ar" and j["intent"] == "slow" and j["outcome"] == "problem_detected"
    assert "الذاكرة على وشك النفاد" in j["answer"] and "الأسباب المحتملة" in j["answer"]
    assert "Likely causes" not in j["answer"] and "Memory is almost" not in j["answer"]


def test_qwen_unavailable_still_gives_a_complete_deterministic_report_and_with_qwen_streams_an_explanation(w):
    j = run(w, SLOW)
    assert j["model"] is None and "AI explanation" not in j["answer"] and "Likely causes" in j["answer"] and j["tokens_in"] == 0
    st = w.use_ollama()
    j = run(w, SLOW)
    assert j["model"] == "qwen2.5:1.5b" and "AI explanation (local model qwen2.5:1.5b)" in j["answer"] and "short of memory" in j["answer"]
    assert j["tokens_in"] == 50 and j["tokens_out"] == 10
    assert any(b["messages"][1]["content"].startswith("<data>") for b in st.seen)               # evidence is fenced as data for the model


def test_failed_repair_is_rolled_back_and_marked_unresolved(w):
    set_level(w, 2)
    w.box.s.update(spooler=False, free=7000, chrome=300, temp=100)
    w.box.fail.add("start")
    j = run(w, "printing stopped working, check the services")
    a = next(x for x in j["actions"] if x["workflow"] == "service_start_safe")
    assert a["status"] == "failed" and a["verify"]["result"] == "fail" and a["verify"]["rollback"]["done"]
    assert any("Stop-Service" in c and "Spooler" in c for c in w.box.executed)                 # the typed inverse ran
    assert j["resolution"] == "unresolved" and "rolled back" in j["answer"]
    assert w.db.one("SELECT COUNT(*) AS n FROM audit WHERE action='agent.rollback'")["n"] == 1


def test_successful_safe_service_repair_is_verified_from_fresh_evidence(w):
    set_level(w, 2)
    w.box.s.update(spooler=False, free=7000, chrome=300, temp=100)
    j = run(w, "printing stopped working, check the services")
    a = next(x for x in j["actions"] if x["workflow"] == "service_start_safe")
    assert a["status"] == "ok" and a["verify"]["result"] == "pass" and j["resolution"] == "resolved"


def test_emergency_stop_cancels_pending_actions_and_blocks_new_work_until_the_owner_resumes(w):
    set_level(w, 3)
    j = run(w, SLOW, client=w.mint("technician", [w.a]))
    assert j["status"] == "awaiting_approval"
    assert w.owner.post("/emergency-stop").status_code == 200
    j = w.owner.get(f"/orgs/{w.a}/agent/sessions/{j['id']}").json()
    assert j["status"] == "cancelled" and not [a for a in j["actions"] if a["status"] in ("pending_approval", "queued", "running")]
    assert any(a["workflow"] == "app_close" and a["status"] == "cancelled" for a in j["actions"])
    assert w.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='pending_approval'")["n"] == 0
    r = w.owner.post(f"/orgs/{w.a}/agent/sessions", json={"request": SLOW, "target_kind": "worker", "target": w.wid})
    assert r.status_code == 423
    assert w.mint("administrator", [w.a]).post("/agent/resume").status_code == 403           # only the owner can lift it
    assert w.owner.post("/agent/resume").status_code == 200
    assert run(w, SLOW)["status"] == "awaiting_approval"


def test_operator_can_cancel_a_waiting_session(w):
    set_level(w, 3)
    j = run(w, SLOW)
    assert w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/cancel").json()["status"] == "cancelled"
    assert w.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='pending_approval'")["n"] == 0


def test_policy_is_owner_only_level_zero_is_read_only_and_audited(w):
    adm = w.mint("administrator", [w.a])
    assert adm.put(f"/orgs/{w.a}/agent/policy", json={"level": 3}).status_code == 403
    assert w.owner.put(f"/orgs/{w.a}/agent/policy", json={"level": 7}).status_code == 422
    set_level(w, 0)
    j = run(w, SLOW)
    assert all(p["decision"] == "observe" for p in j["report"]["repair_plan"]) and j["actions"] == []
    r = w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/repairs", json={"workflow": "temp_cleanup", "finding": "temp_large"})
    assert r.status_code == 403
    assert w.db.one("SELECT COUNT(*) AS n FROM audit WHERE action='agent.policy_changed'")["n"] >= 1


def test_manual_request_of_a_safe_repair_runs_it_but_an_approval_repair_waits(w):
    j = run(w, SLOW)                                                    # level 1: proposals
    r = w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/repairs", json={"workflow": "temp_cleanup", "finding": "temp_large"})
    assert r.status_code == 201
    j2 = settle(w, j["id"], wait=("completed",))
    assert next(a for a in j2["actions"] if a["workflow"] == "temp_cleanup")["status"] == "ok"
    r = w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/repairs", json={"workflow": "app_close", "finding": "mem_pressure"})
    assert r.status_code == 201 and next(a for a in r.json()["actions"] if a["workflow"] == "app_close")["status"] == "pending_approval"
    assert not any("CloseMainWindow" in c for c in w.box.executed)
    r = w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/repairs", json={"workflow": "dns_flush", "finding": "dns_fail"})
    assert r.status_code == 404                                         # only repairs this session proposed can be requested


def test_maintenance_plan_needs_second_person_and_scopes_what_level_four_may_do(w):
    set_level(w, 4)
    tech, admin = w.mint("administrator", [w.a]), w.mint("administrator", [w.a])
    j = run(w, SLOW)
    ac = next(a for a in j["actions"] if a["workflow"] == "app_close")
    assert ac["auto"] == 0 and ac["status"] == "pending_approval"                                                 # no plan yet: level 4 still needs a person for app_close
    w.owner.post(f"/orgs/{w.a}/agent/sessions/{j['id']}/cancel")
    p = tech.post(f"/orgs/{w.a}/agent/maintenance", json={"title": "Night clean", "targets": [w.wid], "workflows": ["app_close"], "hours": 2}).json()
    assert tech.post(f"/orgs/{w.a}/agent/maintenance/{p['id']}/approve").status_code == 403                  # the author cannot approve
    assert w.owner.post(f"/orgs/{w.a}/agent/maintenance/{p['id']}/approve").json()["status"] == "active"
    assert tech.post(f"/orgs/{w.a}/agent/maintenance", json={"title": "x" * 5, "targets": [w.wid], "workflows": ["reboot"], "hours": 1}).status_code == 422   # disruptive can never be scheduled
    w.box.s.update(chrome=3500, free=400)
    j = run(w, SLOW)
    a = next(x for x in j["actions"] if x["workflow"] == "app_close")
    assert a["auto"] == 1 and a["status"] == "ok"                                                                # inside the approved window
    other = w.owner.post(f"/orgs/{w.a}/agent/maintenance", json={"title": "Other", "targets": ["wst_nope"], "workflows": ["app_close"], "hours": 1})
    assert other.status_code == 422                                                                              # device boundary


def test_no_other_client_can_see_or_drive_a_session_and_viewers_cannot_start(w):
    j = run(w, SLOW)
    other = w.mint("administrator", [w.b])
    assert other.get(f"/orgs/{w.a}/agent/sessions/{j['id']}").status_code == 403
    assert other.post(f"/orgs/{w.b}/agent/sessions", json={"request": SLOW, "target_kind": "worker", "target": w.wid}).status_code == 422     # a's computer
    assert w.mint("viewer", [w.a]).post(f"/orgs/{w.a}/agent/sessions", json={"request": SLOW, "target_kind": "worker", "target": w.wid}).status_code == 403


def test_main_pc_is_owner_only_and_runs_locally(w):
    tech = w.mint("technician", [w.a])
    assert tech.post(f"/orgs/{w.a}/agent/sessions", json={"request": SLOW, "target_kind": "main", "target": "main"}).status_code == 403
    set_level(w, 2)
    j = run(w, SLOW, kind="main", target="main")
    assert j["status"] == "completed" and j["outcome"] == "problem_detected" and w.local["calls"] and not w.box.executed
    assert next(a for a in j["actions"] if a["workflow"] == "temp_cleanup")["status"] == "ok"


def test_only_one_session_per_device_at_a_time(w):
    r1 = w.owner.post(f"/orgs/{w.a}/agent/sessions", json={"request": SLOW, "target_kind": "worker", "target": w.wid})
    r2 = w.owner.post(f"/orgs/{w.a}/agent/sessions", json={"request": SLOW, "target_kind": "worker", "target": w.wid})
    assert r1.status_code == 201 and r2.status_code == 409
    settle(w, r1.json()["id"])


def test_network_playbook_finds_dns_problem_and_flushes_it_at_level_two(w):
    set_level(w, 2)
    w.box.s.update(dns_ok=False, free=7000, chrome=300, temp=100)
    j = run(w, "Check why the internet disconnects")
    assert j["intent"] == "network" and j["report"]["causes"][0]["code"] == "dns_fail"
    a = next(x for x in j["actions"] if x["workflow"] == "dns_flush")
    assert a["status"] == "ok" and a["verify"]["result"] in ("pass", "inconclusive") and j["resolution"] in ("resolved", "partially_resolved")
    assert j["report"]["after"]["dns_ms"] == 30


# ---- worker connectivity / android ------------------------------------------------------------------------------------------------------
def test_worker_connectivity_check_reports_real_state_and_can_reconnect(w):
    set_level(w, 2)
    j = run(w, "Check CIT Worker and reconnect it")
    assert j["intent"] == "cit_worker" and j["outcome"] == "unreachable"                       # the test worker really is unreachable (127.0.0.1:1)
    assert any(f["code"] == "worker_unreachable" for f in j["report"]["findings"])
    a = next(x for x in j["actions"] if x["workflow"] == "worker_reconnect")
    assert a["status"] in ("ok", "failed") and a["verify"]["result"] == "fail"                 # reconnect was tried, verification honestly failed
    assert "Device unreachable" in j["answer"]


class FakeAdb:
    def __init__(self):
        self.devices, self.restarted = [("ABC12345", "unauthorized", "CPH1823")], 0

    def __call__(self, args, timeout):
        if args[0] == "devices":
            return 0, ("List of devices attached\n" + "".join(f"{s}\t{st} model:{m}\n" for s, st, m in self.devices)).encode()
        if args[0] == "kill-server":
            self.restarted += 1; return 0, b""
        if args[0] == "start-server":
            return 0, b""
        if "logcat" in args:
            return 0, b"E AndroidRuntime: FATAL EXCEPTION: main\nE AndroidRuntime: Process: com.demo.shop, PID: 99\nE AndroidRuntime: java.lang.NullPointerException\n"
        if "dumpsys" in args:
            return 0, b"  level: 12\n  status: 3\n"
        if "wm" in args and "size" in args:
            return 0, b"Physical size: 720x1520\n"
        return 0, b"Samsung\n"


def test_android_not_detected_explains_authorization_and_assignment_and_never_bypasses(w):
    adb = FakeAdb(); w.app.state.android.local_run = adb
    set_level(w, 2)
    j = run(w, "Why is my Android device not detected?", kind="android", target="main:")
    codes = {f["code"] for f in j["report"]["findings"]}
    assert "android_unassigned" in codes                                           # attached but the owner has not assigned it to this client


def test_android_unauthorized_and_no_device_flows(w):
    adb = FakeAdb(); w.app.state.android.local_run = adb
    cols = [c["name"] for c in w.db.all("PRAGMA table_info(android_devices)")]
    vals = {"host": "main", "serial": "ABC12345", "org_id": w.a, "name": "Test phone", "assigned_by": "owner", "assigned_at": time.time()}
    w.db.execute(f"INSERT INTO android_devices({','.join(k for k in vals if k in cols)}) VALUES({','.join('?' for k in vals if k in cols)})", tuple(v for k, v in vals.items() if k in cols))
    set_level(w, 2)
    j = run(w, "Why is my Android device not detected?", kind="android", target="main:ABC12345")
    f = {x["code"]: x for x in j["report"]["findings"]}
    assert "adb_unauthorized" in f and "cannot bypass" in f["adb_unauthorized"]["next"] and j["outcome"] == "problem_detected"
    adb.devices = [("ABC12345", "device", "CPH1823")]
    j = run(w, "Find the cause of this application crash on the phone", kind="android", target="main:ABC12345")
    f = {x["code"]: x for x in j["report"]["findings"]}
    assert f["android_app_crash"]["p"]["app"] == "com.demo.shop" and "android_battery_low" in f
    adb.devices = []
    j = run(w, "Why is my Android device not detected?", kind="android", target="main:")
    assert any(x["code"] == "adb_no_devices" for x in j["report"]["findings"])
    a = next(x for x in j["actions"] if x["workflow"] == "adb_restart")
    assert adb.restarted == 1 and a["status"] == "ok" and a["verify"]["result"] == "fail"       # restarted, and verification honestly says nothing appeared


# ---- models -----------------------------------------------------------------------------------------------------------------------------
def test_model_registry_is_honest_about_a_small_model_and_benchmarks_are_measured(w):
    w.use_ollama()
    ms = w.owner.get(f"/orgs/{w.a}/agent/models").json()
    m = next(x for x in ms if x["host"] == "main" and x["model"])
    assert m["model"] == "qwen2.5:1.5b" and m["tool_calling"] == "poor" and m["arabic"] == "poor" and "deterministic" in m["usable_for"] and m["hardware_fit"] in ("fits", "tight", "too large", "unknown")
    b = w.owner.post(f"/orgs/{w.a}/agent/models/benchmark", json={"host": "main", "model": "qwen2.5:1.5b"}).json()
    assert b["json_ok"] is True and b["arabic_ok"] is False                       # the stub answers English: measured, not assumed
    m = next(x for x in w.owner.get(f"/orgs/{w.a}/agent/models").json() if x["model"])
    assert m["benchmark"]["arabic_ok"] is False
    assert w.owner.post(f"/orgs/{w.a}/agent/models/benchmark", json={"host": "main", "model": "nope:1b"}).status_code == 404


# ---- knowledge --------------------------------------------------------------------------------------------------------------------------
def test_verified_cases_are_stored_found_again_and_rejected_cases_are_not_reused(w):
    set_level(w, 2)
    j1 = run(w, SLOW)
    cases = w.owner.get(f"/orgs/{w.a}/agent/kb/cases").json()
    assert len(cases) == 1 and cases[0]["status"] == "verified" and cases[0]["workflow"] == "temp_cleanup" and cases[0]["root_cause"] == "Memory is almost exhausted"
    w.box.s.update(temp=2500, chrome=3500, free=400)
    j2 = run(w, "my computer is slow again")
    mem = [e for e in j2["events"] if e["kind"] == "memory"]
    assert mem and mem[0]["data"][0]["id"] == cases[0]["id"]                                       # the earlier case was retrieved
    assert w.owner.get(f"/orgs/{w.a}/agent/kb/cases", params={"q": "computer slow memory"}).json()
    r = w.owner.post(f"/orgs/{w.a}/agent/kb/cases/{cases[0]['id']}/feedback", json={"delta": -1, "note": "wrong"}).json()
    assert r["status"] == "rejected"
    assert not w.owner.get(f"/orgs/{w.a}/agent/kb/cases", params={"q": "computer slow memory"}).json() or all(c["id"] != cases[0]["id"] for c in w.owner.get(f"/orgs/{w.a}/agent/kb/cases", params={"q": "computer slow memory"}).json())


def test_unverified_experience_is_labelled_unverified_and_not_trusted(w):
    j = run(w, SLOW)                       # level 1: nothing repaired
    c = w.owner.get(f"/orgs/{w.a}/agent/kb/cases").json()[0]
    assert c["status"] == "unverified" and c["verified"] == 0
    assert w.agent.kb.stats(w.a, "mem_pressure", "app_close") == {"ok": 0, "bad": 0, "confidence": 0.5}


def test_imported_knowledge_is_validated_inert_until_approved_and_cannot_add_actions(w):
    good = {"format": "cit-knowledge", "version": 1, "procedures": [{"key": "slow-mem", "title": "Memory pressure runbook", "signature": ["mem_pressure"], "workflow": "app_close", "body": "Check the largest process first."}], "cases": []}
    tech, admin = w.mint("technician", [w.a]), w.mint("administrator", [w.a])
    assert tech.post(f"/orgs/{w.a}/agent/kb/import", json={"bundle": good}).status_code == 403                        # importing is an administrator action
    r = admin.post(f"/orgs/{w.a}/agent/kb/import", json={"bundle": good})
    assert r.status_code == 200 and "proposed" in r.json()["status"]
    procs = [p for p in w.owner.get(f"/orgs/{w.a}/agent/kb/procedures").json() if p["source"] == "import"]
    assert procs[0]["status"] == "proposed"
    bad = {**good, "procedures": [{**good["procedures"][0], "workflow": "format_c_drive", "body": "Remove-Item C:\\ -Recurse"}]}
    assert admin.post(f"/orgs/{w.a}/agent/kb/import", json={"bundle": bad}).status_code == 422                       # cannot reference anything outside the reviewed library
    assert admin.post(f"/orgs/{w.a}/agent/kb/import", json={"bundle": {"format": "other"}}).status_code == 422
    assert admin.post(f"/orgs/{w.a}/agent/kb/import", json={"bundle": good, "restore": True}).status_code == 403           # restore is the owner's
    pid = procs[0]["id"]
    assert tech.post(f"/orgs/{w.a}/agent/kb/procedures/{pid}/approve").status_code == 403                                   # role: administrators review
    assert admin.post(f"/orgs/{w.a}/agent/kb/procedures/{pid}/approve").status_code == 403                                  # the importer (an administrator) cannot approve their own import
    assert w.mint("administrator", [w.a]).post(f"/orgs/{w.a}/agent/kb/procedures/{pid}/approve").json()["status"] == "approved"
    exp = w.owner.get(f"/orgs/{w.a}/agent/kb/export").json()
    assert exp["format"] == "cit-knowledge" and exp["procedures"] and "wst_" not in json.dumps([exp["procedures"], exp["cases"]]) and "address" not in json.dumps([exp["procedures"], exp["cases"]])
    # a new version replaces the approved one; old becomes retired
    p2 = tech.post(f"/orgs/{w.a}/agent/kb/procedures", json={"key": "slow-mem", "title": "Memory runbook v2", "signature": ["mem_pressure"], "workflow": "app_close", "body": "x"}).json()
    assert p2["version"] == 2
    assert w.owner.post(f"/orgs/{w.a}/agent/kb/procedures/{p2['id']}/approve").json()["status"] == "approved"
    assert next(p for p in w.owner.get(f"/orgs/{w.a}/agent/kb/procedures").json() if p["id"] == pid)["status"] == "retired"


def test_restore_keeps_status_only_for_the_owner_and_round_trips(w):
    set_level(w, 2)
    run(w, SLOW)
    bundle = w.owner.get(f"/orgs/{w.a}/agent/kb/export").json()
    r = w.owner.post(f"/orgs/{w.b}/agent/kb/import", json={"bundle": bundle, "restore": True})
    assert r.status_code == 200 and r.json()["cases"] == 1
    c = w.owner.get(f"/orgs/{w.b}/agent/kb/cases").json()
    assert c and c[0]["status"] == "verified"
    r = w.owner.post(f"/orgs/{w.b}/agent/kb/import", json={"bundle": bundle})            # plain import: never trusted
    assert [x for x in w.owner.get(f"/orgs/{w.b}/agent/kb/cases").json() if x["status"] == "unverified"]


def test_model_text_can_never_become_an_action_or_a_parameter(w):
    # a hostile service display name / process name from the computer must not alter what runs
    w.box.s.update(spooler=False, free=7000, chrome=300, temp=100)
    orig = w.box.run
    def hostile(sc):
        if "Get-Service | Select-Object Name" in sc:
            return json.dumps([{"Name": "Spooler", "DisplayName": "'; Remove-Item C:\\ -Recurse #", "Status": 1, "StartType": 2}, {"Name": "Evil'; calc;'", "DisplayName": "x", "Status": 1, "StartType": 2}]), 0
        return orig(sc)
    w.box.run = hostile
    set_level(w, 2)
    run(w, "printing stopped working, check the services")
    assert not any("Remove-Item C" in c or "calc" in c for c in w.box.executed)


def test_proactive_monitoring_is_off_by_default_owner_only_and_opens_a_normal_session_once(w):
    assert w.agent.monitor_tick() == 0                                                   # nothing enabled
    assert w.mint("administrator", [w.a]).put(f"/orgs/{w.a}/agent/monitor", json={"enabled": True}).status_code == 403
    assert w.owner.put(f"/orgs/{w.a}/agent/monitor", json={"enabled": True}).json() == {"enabled": True}
    w.app.state.workers.get = lambda wid: {"state": "online"}                            # the monitor only looks at online computers
    w.box.s.update(free=200)
    assert w.agent.monitor_tick() == 1                                                   # memory is critical -> one incident
    sid = w.owner.get(f"/orgs/{w.a}/agent/sessions").json()[0]["id"]
    j = settle(w, sid)
    assert j["actor"] == "monitor" and j["report"]["causes"][0]["code"] == "mem_pressure" and j["actions"] == []        # level 1: it only diagnoses
    assert w.agent.monitor_tick() == 0                                                   # cooldown: no repeated incident for the same computer
    w.box.s.update(free=7000)
