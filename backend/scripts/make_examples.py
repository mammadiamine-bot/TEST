"""Generate example reports from the MOCK devices (no real computer involved)."""
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core

out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
core = Core(Settings(data_dir=pathlib.Path(tempfile.mkdtemp()), api_token="x"), Db(":memory:"), cloud_probe=lambda: False)
c = TestClient(create_app(core.s, core), headers={"Authorization": "Bearer x"})
d = c.post("/devices", json={"name": "Mock office PC", "address": "mock", "transport": "mock", "owner_name": "Example Client", "owner_contact": "client@example.com", "authorization_note": "Example record (mock)"}).json()
c.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"]})
s = c.post(f"/devices/{d['id']}/sessions", json={"scope": "repair", "minutes": 30}).json()
c.post(f"/sessions/{s['id']}/consent", json={"code": s["consent_code"], "person": "Example Client"})
run = c.post(f"/devices/{d['id']}/diagnose", json={}).json()
(out / "diagnostic-run.json").write_text(json.dumps(run, indent=2))
(out / "report-diagnostic-only.pdf").write_bytes(c.get(f"/devices/{d['id']}/report", params={"run_id": run["id"]}).content)
p = c.post(f"/devices/{d['id']}/plans", json={"run_id": run["id"]}).json()
c.post(f"/plans/{p['id']}/approve-low-risk", json={"approver": "Technician"})
for st in p["steps"]:
    if st["tool_id"] == "clear_temp_files":
        c.post(f"/plans/{p['id']}/steps/{st['id']}/approve", json={"approver": "Technician", "confirm_high_risk": True})
c.post(f"/devices/{d['id']}/notes", json={"author": "Technician", "body": "Mock example note."})
c.post(f"/plans/{p['id']}/execute")
(out / "report-before-after.pdf").write_bytes(c.get(f"/devices/{d['id']}/report", params={"plan_id": p["id"]}).content)
print("written", sorted(f.name for f in out.iterdir()))
