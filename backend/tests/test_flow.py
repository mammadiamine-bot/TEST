import pytest

from app import ai, diagnostics as dx
from app.service import ssh_fingerprint
from tests.conftest import onboard
import base64, hashlib


def test_pairing_wrong_code_and_throttle(client):
    d = client.post("/devices", json={"name": "A", "address": "mock", "transport": "mock", "owner_name": "o", "owner_contact": "o@x.io",
                                      "authorization_note": "owner consented in writing"}).json()
    codes = []
    for _ in range(5):
        codes.append(client.post(f"/devices/{d['id']}/pair", json={"code": "000000"}).status_code)
    assert set(codes) <= {403}
    assert client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"]}).status_code == 429  # even the right code is throttled


def test_pairing_expires(client, clock):
    d = client.post("/devices", json={"name": "A", "address": "mock", "transport": "mock", "owner_name": "o", "owner_contact": "o@x.io",
                                      "authorization_note": "owner consented in writing"}).json()
    clock.t += 601
    assert client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"]}).status_code == 410


def test_ssh_pairing_requires_matching_fingerprint(client, core):
    d = client.post("/devices", json={"name": "S", "address": "192.168.1.50", "transport": "ssh", "owner_name": "o",
                                      "owner_contact": "o@x.io", "authorization_note": "owner consented in writing"}).json()
    blob = base64.b64encode(b"fake-host-key-bytes").decode()
    line = f"192.168.1.50 ssh-ed25519 {blob}"
    fp = ssh_fingerprint(line)
    assert fp == "SHA256:" + base64.b64encode(hashlib.sha256(b"fake-host-key-bytes").digest()).decode().rstrip("=")
    bad = client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"], "fingerprint": "SHA256:attacker", "host_key": line})
    assert bad.status_code == 403
    ok = client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"], "fingerprint": fp, "host_key": line})
    assert ok.status_code == 200 and ok.json()["fingerprint"] == fp


def test_no_access_without_consented_session(client):
    d = client.post("/devices", json={"name": "A", "address": "mock", "transport": "mock", "owner_name": "o", "owner_contact": "o@x.io",
                                      "authorization_note": "owner consented in writing"}).json()
    assert client.post(f"/devices/{d['id']}/diagnose", json={}).status_code == 403  # not paired/consented
    client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"]})
    s = client.post(f"/devices/{d['id']}/sessions", json={"scope": "diagnose", "minutes": 10}).json()
    assert client.post(f"/devices/{d['id']}/diagnose", json={}).status_code == 403  # requested but not consented
    assert client.post(f"/sessions/{s['id']}/consent", json={"code": "111111", "person": "x"}).status_code in (403,)
    assert client.get(f"/devices/{d['id']}/metrics").status_code == 403


def test_session_expires_and_revoke(client, clock):
    did, sid = onboard(client, scope="diagnose", minutes=5)
    assert client.post(f"/devices/{did}/diagnose", json={"modules": ["cpu_ram"]}).status_code == 200
    clock.t += 301
    assert client.post(f"/devices/{did}/diagnose", json={"modules": ["cpu_ram"]}).status_code == 403
    did, sid = onboard(client, scope="diagnose")
    assert client.post(f"/sessions/{sid}/revoke").json()["status"] == "revoked"
    assert client.get(f"/devices/{did}/metrics").status_code == 403


def test_revoke_device_blocks_everything(client):
    did, _ = onboard(client)
    client.post(f"/devices/{did}/revoke")
    assert client.post(f"/devices/{did}/diagnose", json={}).status_code == 403


def test_all_modules_broken_scenario_ranked(client):
    did, _ = onboard(client)
    run = client.post(f"/devices/{did}/diagnose", json={}).json()
    assert run["errors"] == {} and set(run["modules"]) == set(dx.MODULES) and run["source"] == "scripted"
    sev = [f["severity"] for f in run["findings"]]
    assert sev == sorted(sev, key=dx.SEV_ORDER.get) and sev[0] == "critical"
    mods = {f["module"] for f in run["findings"]}
    assert {"event_log", "storage", "printers", "windows_update", "dns_dhcp", "integrity", "startup", "drivers", "cpu_ram"} <= mods


def test_healthy_scenario_has_no_actionable_findings(client):
    did, _ = onboard(client, scenario="healthy")
    run = client.post(f"/devices/{did}/diagnose", json={}).json()
    assert [f for f in run["findings"] if f["severity"] != "info"] == []


def _plan(client, did):
    run = client.post(f"/devices/{did}/diagnose", json={}).json()
    return client.post(f"/devices/{did}/plans", json={"run_id": run["id"]}).json()


def test_nothing_executes_without_approval(client, core):
    did, _ = onboard(client)
    p = _plan(client, did)
    assert client.post(f"/plans/{p['id']}/execute").status_code == 409
    assert core._mock(did).executed == []


def test_high_risk_needs_per_action_confirmation_and_bulk_skips_it(client, core):
    did, _ = onboard(client)
    p = _plan(client, did)
    high = [s for s in p["steps"] if s["risk"] == "high"]
    assert high
    assert client.post(f"/plans/{p['id']}/steps/{high[0]['id']}/approve", json={"approver": "Tech"}).status_code == 428
    bulk = client.post(f"/plans/{p['id']}/approve-low-risk", json={"approver": "Tech"}).json()
    assert all(s["status"] == "proposed" for s in bulk["steps"] if s["risk"] == "high")
    assert all(s["status"] == "approved" for s in bulk["steps"] if s["risk"] == "low")


def test_full_repair_flow_verified_before_after(client, core):
    did, _ = onboard(client)
    p = _plan(client, did)
    client.post(f"/plans/{p['id']}/approve-low-risk", json={"approver": "Tech"})
    for s in [s for s in p["steps"] if s["risk"] == "high"]:
        client.post(f"/plans/{p['id']}/steps/{s['id']}/approve", json={"approver": "Tech", "confirm_high_risk": True})
    done = client.post(f"/plans/{p['id']}/execute").json()
    ran = [s for s in done["steps"] if s["status"] != "rejected"]
    assert ran and all(s["status"] == "verified" for s in ran), [(s["tool_id"], s["status"], s["result"]) for s in ran]
    assert done["status"] == "completed"
    before = {f["id"] for f in done["before_findings"]}
    after = {f["id"] for f in done["after"]["findings"]}
    assert {"printers.spooler_down", "dns_dhcp.dns", "storage.low_space", "windows_update.failures"} <= before
    assert not ({"printers.spooler_down", "dns_dhcp.dns"} & after)
    # honest re-scan: space improved but is still below 10% free, so the finding stays with a lower severity
    sev = lambda fs: next(f["severity"] for f in fs if f["id"] == "storage.low_space")
    assert sev(done["before_findings"]) == "high" and sev(done["after"]["findings"]) == "medium"
    assert core.db.verify_audit_chain()


def test_ineffective_repair_is_not_reported_as_success(client, core):
    did, _ = onboard(client)
    core._mock(did).ineffective.add("restart_spooler")
    p = _plan(client, did)
    sp = next(s for s in p["steps"] if s["tool_id"] == "restart_spooler")
    client.post(f"/plans/{p['id']}/steps/{sp['id']}/approve", json={"approver": "Tech"})
    done = client.post(f"/plans/{p['id']}/execute").json()
    st = next(s for s in done["steps"] if s["id"] == sp["id"])
    assert st["status"] == "unverified" and st["result"]["verified"] is False
    assert done["status"] == "completed_with_issues"
    assert "printers.spooler_down" in {f["id"] for f in done["after"]["findings"]}


def test_failed_tool_marked_failed(client, core):
    did, _ = onboard(client)
    core._mock(did).fail_tools.add("flush_dns")
    p = _plan(client, did)
    s = next(s for s in p["steps"] if s["tool_id"] == "flush_dns")
    client.post(f"/plans/{p['id']}/steps/{s['id']}/approve", json={"approver": "T"})
    done = client.post(f"/plans/{p['id']}/execute").json()
    assert next(x for x in done["steps"] if x["id"] == s["id"])["status"] == "failed"


def test_diagnose_scope_cannot_repair(client, core):
    did, _ = onboard(client, scope="diagnose")
    p = _plan(client, did)
    s = next(s for s in p["steps"] if s["risk"] == "low")
    client.post(f"/plans/{p['id']}/steps/{s['id']}/approve", json={"approver": "T"})
    assert client.post(f"/plans/{p['id']}/execute").status_code == 403
    assert core._mock(did).executed == []


def test_tampered_step_after_approval_is_refused(client, core):
    did, _ = onboard(client)
    p = _plan(client, did)
    s = next(s for s in p["steps"] if s["tool_id"] == "disable_startup_item")
    client.post(f"/plans/{p['id']}/steps/{s['id']}/approve", json={"approver": "T"})
    stored = core.plan(p["id"])
    next(x for x in stored["steps"] if x["id"] == s["id"])["params"] = {"name": "App1"}
    next(x for x in stored["steps"] if x["id"] == s["id"])["params"] = {"name": "Other"}
    core._save_plan(stored)
    done = client.post(f"/plans/{p['id']}/execute").json()
    assert next(x for x in done["steps"] if x["id"] == s["id"])["status"] == "failed"
    assert core._mock(did).executed == []


def test_identity_mismatch_blocks(client, core):
    did, _ = onboard(client)
    core._mock(did).host_fingerprint = lambda: "different"
    assert client.post(f"/devices/{did}/diagnose", json={"modules": ["cpu_ram"]}).json()["errors"]["cpu_ram"].startswith("device identity mismatch")


def test_console_flow(client):
    did, _ = onboard(client)
    assert client.post(f"/devices/{did}/console", json={"command": "Get-Service"}).json()["ok"]
    assert client.post(f"/devices/{did}/console", json={"command": "Get-Service | Stop-Service"}).status_code == 422


def test_audit_chain_detects_tampering(client, core):
    onboard(client)
    assert core.db.verify_audit_chain()
    core.db.execute("UPDATE audit SET detail='{}' WHERE seq=2")
    assert not core.db.verify_audit_chain()


def test_offline_mode_labels_scripted_and_queues_sanitized(client, core, online):
    did, _ = onboard(client)
    client.post(f"/devices/{did}/diagnose", json={})
    online["v"] = False
    r = client.post("/chat", json={"device_id": did, "message": "why is my PC slow? ip 192.168.1.20"}).json()
    assert r["source"] == "scripted" and r["cloud_available"] is False and "unavailable" in r["notice"]
    q = core.db.all("SELECT payload FROM offline_queue")
    assert len(q) == 1 and "192.168" not in q[0]["payload"] and "evidence" not in q[0]["payload"]
    assert client.get("/status").json()["mode"] == "offline"


def test_diagnostics_work_offline(client, online):
    did, _ = onboard(client)
    online["v"] = False
    assert client.post(f"/devices/{did}/diagnose", json={}).status_code == 200


def test_redaction():
    t = ai.redact("host 192.168.1.20 mac AA:BB:CC:DD:EE:FF mail a@b.com C:\\Users\\Sara\\x")
    assert "192.168" not in t and "AA:BB" not in t and "a@b.com" not in t and "Sara" not in t


def test_pdf_report(client):
    did, _ = onboard(client)
    p = _plan(client, did)
    client.post(f"/plans/{p['id']}/approve-low-risk", json={"approver": "T"})
    client.post(f"/plans/{p['id']}/execute")
    client.post(f"/devices/{did}/notes", json={"author": "Tech", "body": "Replaced cable"})
    r = client.get(f"/devices/{did}/report", params={"plan_id": p["id"]})
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and len(r.content) > 1500


def test_metrics_live(client):
    did, _ = onboard(client)
    m = client.get(f"/devices/{did}/metrics").json()
    assert {"cpu_percent", "ram_percent"} <= set(m)
