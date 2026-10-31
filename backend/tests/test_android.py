import base64
import io
import json
import re
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.android import read_only_shell
from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from tests.test_tasks import XorProtector

PNG = io.BytesIO()
Image.new("RGB", (1080, 2400), (10, 120, 200)).save(PNG, "PNG")
APK = b"PK\x03\x04" + b"0" * 2000


class FakeAdb:
    """Plays an Android phone: serial PHONE0001 (authorized) and PHONE0002 (unauthorized)."""
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(args)
        a = " ".join(args)
        if a == "devices -l":
            return 0, b"List of devices attached\nPHONE0001 device product:p model:Pixel_7 transport_id:1\nPHONE0002 unauthorized transport_id:2\n"
        if "wm size" in a:
            return 0, b"Physical size: 1080x2400\n"
        if "wm density" in a:
            return 0, b"Physical density: 420\n"
        if "exec-out screencap" in a:
            return 0, PNG.getvalue()
        if "getprop ro.product.model" in a:
            return 0, b"Pixel 7\n"
        if "getprop ro.build.version.release" in a:
            return 0, b"14\n"
        if "getprop" in a:
            return 0, b"x\n"
        if "dumpsys battery" in a:
            return 0, b"Current Battery Service state:\n  level: 77\n  status: 2\n  powered: true\n"
        if "pm list packages" in a:
            return 0, b"package:com.example.app\npackage:org.test.notes\n"
        if "logcat" in a:
            return 0, b"01-01 00:00:00.000 W/Tag: something\n"
        if " install " in f" {a} ":
            return 0, b"Success\n"
        return 0, b""


@pytest.fixture
def w(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(tmp_path / "c.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    app = create_app(s, core)
    fake = FakeAdb(); app.state.android.local_run = fake
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    a = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]; b = owner.post("/orgs", json={"name": "Beta"}).json()["id"]
    core.db.execute("INSERT INTO workers(id,name,address,fingerprint,status,created_at,org_id) VALUES('wst_a0000000','a-pc','127.0.0.1:1',?, 'enrolled',?,?)", ("ab" * 32, time.time(), a))
    mint = lambda role, orgs: TestClient(app, headers={"Authorization": "Bearer " + owner.post("/principals", json={"name": role, "role": role, "org_ids": orgs}).json()["token"]})  # noqa: E731
    return type("W", (), dict(app=app, db=core.db, owner=owner, a=a, b=b, fake=fake, mint=mint, core=core))


def base(w, org=None, serial="PHONE0001", host="main"):
    return f"/orgs/{org or w.a}/android/{host}/{serial}"


def test_discovery_needs_assignment_and_reports_unauthorized_honestly(w):
    assert [d["serial"] for d in w.owner.get("/android/unassigned").json()] == ["PHONE0001", "PHONE0002"]
    for s in ("PHONE0001", "PHONE0002"):
        assert w.owner.put(f"/orgs/{w.a}/android/assign", json={"serial": s, "name": s}).status_code == 200
    d = {x["serial"]: x for x in w.owner.get(f"/orgs/{w.a}/android/devices").json() if x.get("host") == "main"}
    assert d["PHONE0001"]["state"] == "device" and d["PHONE0001"]["model"] == "Pixel_7" and d["PHONE0002"]["state"] == "unauthorized"
    assert w.owner.get(f"/orgs/{w.b}/android/devices").json() == []
    ta = w.mint("technician", [w.a])
    assert ta.get("/android/unassigned").status_code == 403 and ta.put(f"/orgs/{w.a}/android/assign", json={"serial": "PHONE0009"}).status_code == 403


def test_info_screen_and_touch_mapping(w):
    w.owner.put(f"/orgs/{w.a}/android/assign", json={"serial": "PHONE0001"})
    i = w.owner.get(base(w) + "/info").json()
    assert i["model"] == "Pixel 7" and i["android"] == "14" and i["screen"] == [1080, 2400] and i["battery"]["level"] == "77"
    r = w.owner.get(base(w) + "/screen?w=400")
    assert r.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(r.content)).width == 400
    w.owner.post(base(w) + "/input", json={"kind": "tap", "x": 0.5, "y": 0.5}); assert ["-s", "PHONE0001", "shell", "input", "tap", "539", "1199"] in w.fake.calls
    w.owner.post(base(w) + "/input", json={"kind": "swipe", "x": 0.5, "y": 0.8, "x2": 0.5, "y2": 0.2}); assert any(c[3:5] == ["input", "swipe"] for c in w.fake.calls)
    w.owner.post(base(w) + "/input", json={"kind": "key", "key": "HOME"}); assert ["-s", "PHONE0001", "shell", "input", "keyevent", "3"] in w.fake.calls
    w.owner.post(base(w) + "/input", json={"kind": "text", "text": "hello world"}); assert ["-s", "PHONE0001", "shell", "input", "text", "hello%sworld"] in w.fake.calls
    assert w.owner.post(base(w) + "/input", json={"kind": "text", "text": "x; rm -rf /"}).status_code == 422
    assert w.owner.post(base(w) + "/input", json={"kind": "key", "key": "REBOOT"}).status_code == 422


def test_read_only_shell_rules():
    for ok in ("getprop ro.product.model", "dumpsys battery", "pm list packages -3", "ls /sdcard", "settings get global airplane_mode_on", "wm size", "top -n 1"):
        assert read_only_shell(ok), ok
    for bad in ("rm -rf /sdcard", "pm uninstall com.x", "reboot", "ls /sdcard; rm x", "cat /a | sh", "logcat -c", "settings put global x 1", "am start -a x", "top", "wm size 1x1", "echo $(id)"):
        assert not read_only_shell(bad), bad


def test_diagnostics_apps_and_logs(w):
    w.owner.put(f"/orgs/{w.a}/android/assign", json={"serial": "PHONE0001"})
    assert w.owner.get(base(w) + "/packages").json() == ["com.example.app", "org.test.notes"]
    assert "something" in w.owner.get(base(w) + "/logcat?lines=50&level=W").json()["output"]
    assert w.owner.post(base(w) + "/shell", json={"cmd": "getprop ro.product.model"}).json()["output"].strip() == "Pixel 7"
    assert w.owner.post(base(w) + "/shell", json={"cmd": "rm -rf /sdcard"}).status_code == 403
    assert w.owner.post(base(w) + "/app", json={"action": "launch", "package": "com.example.app"}).json()["ok"]
    assert w.owner.post(base(w) + "/app", json={"action": "launch", "package": "x; reboot"}).status_code == 422
    assert w.owner.get(base(w) + "/logcat?level=Z").status_code == 422


def test_changes_need_a_second_person_and_reboot_needs_the_phrase(w):
    w.owner.put(f"/orgs/{w.a}/android/assign", json={"serial": "PHONE0001"})
    tech = w.mint("technician", [w.a]); _, adm = None, w.mint("administrator", [w.a]); adm2 = w.mint("administrator", [w.a])
    req = tech.post(base(w) + "/requests", json={"kind": "install", "arg": "app.apk", "apk_b64": base64.b64encode(APK).decode()}).json()
    assert req["status"] == "pending_approval" and not any(" install " in " " + " ".join(c) + " " for c in w.fake.calls)    # nothing ran yet
    assert tech.post(f"/orgs/{w.a}/android/requests/{req['id']}/approve", json={}).status_code == 403                      # technicians cannot approve
    done = adm.post(f"/orgs/{w.a}/android/requests/{req['id']}/approve", json={}).json()
    assert done["status"] == "done" and any(c[:4] == ["-s", "PHONE0001", "install", "-r"] for c in w.fake.calls)
    mine = adm.post(base(w) + "/requests", json={"kind": "uninstall", "arg": "com.example.app"}).json()
    assert adm.post(f"/orgs/{w.a}/android/requests/{mine['id']}/approve", json={}).status_code == 403                        # four-eyes: not your own
    assert adm2.post(f"/orgs/{w.a}/android/requests/{mine['id']}/approve", json={}).json()["status"] == "done"
    rb = tech.post(base(w) + "/requests", json={"kind": "reboot"}).json()
    assert adm.post(f"/orgs/{w.a}/android/requests/{rb['id']}/approve", json={}).status_code == 422
    assert adm.post(f"/orgs/{w.a}/android/requests/{rb['id']}/approve", json={"confirm": "I APPROVE"}).json()["status"] == "done"
    assert tech.post(base(w) + "/requests", json={"kind": "install", "apk_b64": base64.b64encode(b"not an apk").decode()}).status_code == 422
    assert tech.post(base(w) + "/requests", json={"kind": "shell", "arg": "ls; rm x"}).status_code == 422
    assert w.db.verify_audit_chain()


def test_client_isolation_for_phones(w):
    w.owner.put(f"/orgs/{w.a}/android/assign", json={"serial": "PHONE0001"})
    tb, ta, cust = w.mint("technician", [w.b]), w.mint("technician", [w.a]), w.mint("customer", [w.a])
    assert tb.get(base(w) + "/screen").status_code == 403 and tb.get(base(w) + "/info").status_code == 403
    assert w.owner.get(base(w, org=w.b) + "/screen").status_code == 403                    # the phone is assigned to Alpha, not Beta
    assert w.owner.get(f"/orgs/{w.a}/android/main/PHONE0002/info").status_code == 403       # not assigned
    assert ta.get(f"/orgs/{w.a}/android/wst_other/PHONE0001/info").status_code == 403       # a computer that is not the client's own
    assert cust.get(base(w) + "/screen").status_code == 403
    assert w.mint("viewer", [w.a]).post(base(w) + "/input", json={"kind": "key", "key": "HOME"}).status_code == 403


def test_worker_hosted_phone_goes_through_the_authenticated_exec_channel(w):
    seen = {}

    def worker_call(wid, method, path, body=None, actor="x"):
        if path == "/clients":
            raise __import__("app.service", fromlist=["CoreError"]).CoreError("exists", 409)
        if path.endswith("/exec"):
            script = body["argv"][-1]; args = json.loads(base64.b64decode(re.search(r"FromBase64String\('([^']+)'", script).group(1))); seen["args"] = args; seen["script"] = script
            return {"job": "abc123"}
        out = json.dumps({"rc": 0, "b64": base64.b64encode(b"List of devices attached\nWPHONE001\tdevice model:Galaxy\n").decode()})
        return {"output": out, "next": 1, "done": True, "exit": 0}

    w.app.state.android.call = worker_call
    d = w.owner.get(f"/orgs/{w.a}/android/devices").json()
    assert d == [{"serial": "WPHONE001", "state": "device", "model": "Galaxy", "product": None, "transport": None, "host": "wst_a0000000", "host_name": "a-pc", "name": "Galaxy"}]
    assert seen["args"] == ["devices", "-l"] and "ProcessStartInfo" in seen["script"] and "adb.exe" in seen["script"]
    assert w.mint("technician", [w.b]).get(f"/orgs/{w.a}/android/devices").status_code == 403
