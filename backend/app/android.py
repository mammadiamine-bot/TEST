"""Android device management over ADB, on the Main PC ("main") or on a Windows Worker (through its authenticated exec channel).

* The phone must have USB debugging enabled AND be authorized on that PC (the on-phone prompt). Unauthorized/offline devices are shown as such.
* Live screen = repeated JPEG screenshots (scrcpy is not required); touch/keys/text are sent with `adb shell input`.
* Read-only diagnostics run directly. Anything that changes a phone (install, uninstall, clear data, reboot, free-form shell) is queued and needs
  approval by a different administrator (the same four-eyes rule as jobs). ADB's network port is never opened or exposed.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .db import Db, new_id
from .ops import org_client
from .service import CoreError

SERIAL = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")
PKG = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
KEYS = {"HOME": 3, "BACK": 4, "APP_SWITCH": 187, "POWER": 26, "VOLUME_UP": 24, "VOLUME_DOWN": 25, "ENTER": 66, "DEL": 67, "MENU": 82, "TAB": 61, "ESCAPE": 111, "DPAD_UP": 19, "DPAD_DOWN": 20, "DPAD_LEFT": 21, "DPAD_RIGHT": 22}
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 .,@_\-+=:/?!#%]{1,200}$")
META = re.compile(r"[;&|<>$`\\\n\r]")
OUT_CAP = 200_000
ADB_PATHS = (r"C:\Android-Testing\platform-tools\adb.exe", os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"))


def adb_exe() -> str | None:
    return shutil.which("adb") or next((p for p in ADB_PATHS if os.path.exists(p)), None)


_SVC = r"(battery|meminfo|cpuinfo|wifi|connectivity|display|power|netstats|batterystats|usb|window|activity|diskstats|procstats|alarm|location|notification)"
_PKGN = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
# every accepted command is matched as a WHOLE against one strict pattern; there are no free-form arguments, so no flag can change device state
READ_ONLY = [re.compile(x) for x in (
    r"getprop( [A-Za-z0-9._-]{1,80})?", rf"dumpsys {_SVC}", rf"dumpsys (meminfo|package) {_PKGN}",
    r"pm list (packages|features|permissions)( -[3sfde])?( [A-Za-z0-9._]{1,80})?", rf"pm path {_PKGN}",
    r"ls( -[lah]{1,4})?( /(sdcard|storage|proc|system|data/local/tmp)(/[A-Za-z0-9._-]+){0,6}/?)?", r"cat /(proc/(version|cpuinfo|meminfo|uptime|loadavg)|system/build\.prop)",
    r"ps( -A| -e)?", r"top -n 1( -b)?", r"df( -h)?", r"settings get (global|secure|system) [A-Za-z0-9._]{1,60}", r"wm (size|density)",
    r"uname( -a)?", r"date", r"id", r"uptime", r"free", r"getenforce", r"ip (addr|route|link)",
)]


def read_only_shell(cmd: str) -> bool:
    cmd = cmd.strip()
    return bool(cmd) and len(cmd) <= 300 and not META.search(cmd) and ".." not in cmd and any(p.fullmatch(cmd) for p in READ_ONLY)


WRAP = r'''$ErrorActionPreference='Stop'
$a=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__ARGS__')))
$adb=(Get-Command adb -ErrorAction SilentlyContinue).Source
if(-not $adb){ foreach($c in 'C:\Android-Testing\platform-tools\adb.exe',"$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"){ if(Test-Path $c){$adb=$c;break} } }
if(-not $adb){ '{"rc":127,"b64":""}'; exit 0 }
$psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$adb; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.RedirectStandardOutput=$true
$psi.Arguments=(@($a) | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join ' '
$p=[Diagnostics.Process]::Start($psi); $ms=New-Object IO.MemoryStream; $p.StandardOutput.BaseStream.CopyTo($ms); $p.WaitForExit(__MS__) | Out-Null
$bytes=$ms.ToArray()
if('__SHOT__' -eq '1' -and $bytes.Length -gt 1000){ Add-Type -AssemblyName System.Drawing; $img=[Drawing.Image]::FromStream((New-Object IO.MemoryStream(,$bytes))); $w=[Math]::Min(__W__,$img.Width); $h=[int]($img.Height*$w/$img.Width)
  $bmp=New-Object Drawing.Bitmap($w,$h); $g=[Drawing.Graphics]::FromImage($bmp); $g.DrawImage($img,0,0,$w,$h); $o=New-Object IO.MemoryStream
  $enc=[Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg'; $ep=New-Object Drawing.Imaging.EncoderParameters(1); $ep.Param[0]=New-Object Drawing.Imaging.EncoderParameter([Drawing.Imaging.Encoder]::Quality,[long]60)
  $bmp.Save($o,$enc,$ep); $bytes=$o.ToArray() }
'{"rc":'+$p.ExitCode+',"b64":"'+[Convert]::ToBase64String($bytes)+'"}'
'''


class Android:
    def __init__(self, db: Db, worker_call, data_dir: Path, local_run=None):
        self.db, self.call, self.dir = db, worker_call, Path(data_dir) / "android_uploads"
        self.local_run = local_run or self._local

    # ---- host runners -------------------------------------------------------------------------------------------------------
    @staticmethod
    def _local(args: list[str], timeout: int) -> tuple[int, bytes]:
        exe = adb_exe()
        if not exe:
            return 127, b""
        try:
            p = subprocess.run([exe, *args], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, b""
        return p.returncode, p.stdout

    def run(self, org: str, host: str, args: list[str], timeout: int = 25, shot_width: int = 0) -> tuple[int, bytes]:
        if host == "main":
            rc, out = self.local_run(args, timeout)
            if shot_width and rc == 0 and len(out) > 1000:
                from PIL import Image
                im = Image.open(io.BytesIO(out)).convert("RGB"); im.thumbnail((shot_width, 10_000)); b = io.BytesIO(); im.save(b, "JPEG", quality=60); out = b.getvalue()
            return rc, out[:OUT_CAP * 20]
        w = self.db.one("SELECT id FROM workers WHERE id=? AND org_id=?", (host, org))
        if not w:
            raise CoreError("that computer does not belong to this client", 422)
        cid = org_client(org)
        try:
            self.call(host, "POST", "/clients", {"id": cid, "name": "CIT " + cid}, "system")
        except CoreError as e:
            if e.status != 409:
                raise
        script = WRAP.replace("__ARGS__", base64.b64encode(json.dumps(args).encode()).decode()).replace("__MS__", str(timeout * 1000)).replace("__SHOT__", "1" if shot_width else "0").replace("__W__", str(shot_width or 0))
        j = self.call(host, "POST", f"/clients/{cid}/exec", {"argv": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], "timeout": timeout + 20}, "system")["job"]
        out, since, end = "", 0, time.time() + timeout + 25
        while time.time() < end:
            s = self.call(host, "GET", f"/clients/{cid}/jobs/{j}?since={since}", None, "system"); out += s.get("output") or ""; since = s["next"]
            if s["done"]:
                break
            time.sleep(0.25)
        else:
            raise CoreError("the computer did not answer in time", 504)
        try:
            d = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise CoreError("unexpected answer from the computer: " + out.strip()[:120], 502)
        return int(d["rc"]), base64.b64decode(d["b64"])

    def _need(self, rc: int, what: str = "adb") -> None:
        if rc == 127:
            raise CoreError("ADB (Android platform-tools) is not installed on that computer", 409)
        if rc == 124:
            raise CoreError("ADB timed out", 504)

    # ---- discovery ------------------------------------------------------------------------------------------------------------
    def devices(self, org: str, host: str) -> list[dict]:
        rc, out = self.run(org, host, ["devices", "-l"], 15)
        self._need(rc)
        res = []
        for line in out.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or not SERIAL.match(parts[0]):
                continue
            kv = dict(x.split(":", 1) for x in parts[2:] if ":" in x)
            res.append({"serial": parts[0], "state": parts[1], "model": kv.get("model"), "product": kv.get("product"), "transport": kv.get("transport_id"), "host": host})
        return res

    def info(self, org: str, host: str, serial: str) -> dict:
        self._serial(serial)
        def sh(*c):
            rc, out = self.run(org, host, ["-s", serial, "shell", *c], 15)
            self._need(rc); return out.decode(errors="replace").strip()
        props = {k: sh("getprop", k) for k in ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk", "ro.serialno", "ro.build.fingerprint")}
        bat = {}
        for line in sh("dumpsys", "battery").splitlines():
            if ":" in line and line.strip().split(":")[0] in ("level", "status", "powered", "AC powered", "USB powered", "temperature", "health"):
                bat[line.split(":")[0].strip()] = line.split(":")[1].strip()
        return {"serial": serial, "manufacturer": props["ro.product.manufacturer"], "model": props["ro.product.model"], "android": props["ro.build.version.release"], "sdk": props["ro.build.version.sdk"],
                "hardware_serial": props["ro.serialno"], "build": props["ro.build.fingerprint"], "battery": bat, "screen": self.size(org, host, serial), "density": sh("wm", "density").split(":")[-1].strip()}

    def size(self, org: str, host: str, serial: str) -> list[int]:
        rc, out = self.run(org, host, ["-s", serial, "shell", "wm", "size"], 15)
        self._need(rc)
        m = re.findall(r"(\d+)x(\d+)", out.decode(errors="replace"))
        if not m:
            raise CoreError("could not read the screen size (is the device authorized?)", 409)
        return [int(m[-1][0]), int(m[-1][1])]

    @staticmethod
    def _serial(serial: str) -> None:
        if not SERIAL.match(serial):
            raise CoreError("invalid device serial", 422)

    # ---- live screen + input ------------------------------------------------------------------------------------------------
    def screenshot(self, org: str, host: str, serial: str, width: int = 540) -> bytes:
        self._serial(serial)
        rc, out = self.run(org, host, ["-s", serial, "exec-out", "screencap", "-p"], 25, shot_width=min(max(width, 200), 1080))
        self._need(rc)
        if rc != 0 or len(out) < 1000:
            raise CoreError("could not capture the screen (device offline, unauthorized or locked)", 409)
        return out

    def input(self, org: str, host: str, serial: str, ev: dict) -> dict:
        self._serial(serial)
        k = ev.get("kind")
        if k in ("tap", "swipe"):
            w, h = self.size(org, host, serial)
            px = lambda f, m: str(int(min(max(float(f), 0), 1) * (m - 1)))  # noqa: E731  fractions of the screen: no pixel maths in the browser
            args = ["input", "tap", px(ev["x"], w), px(ev["y"], h)] if k == "tap" else ["input", "swipe", px(ev["x"], w), px(ev["y"], h), px(ev["x2"], w), px(ev["y2"], h), str(min(max(int(ev.get("ms", 250)), 50), 3000))]
        elif k == "text":
            if not SAFE_TEXT.match(ev.get("text", "")):
                raise CoreError("only letters, digits and . , @ _ - + = : / ? ! # % can be typed through ADB", 422)
            args = ["input", "text", ev["text"].replace(" ", "%s")]
        elif k == "key":
            if ev.get("key") not in KEYS:
                raise CoreError("unsupported key", 422)
            args = ["input", "keyevent", str(KEYS[ev["key"]])]
        else:
            raise CoreError("unknown input", 422)
        rc, _ = self.run(org, host, ["-s", serial, "shell", *args], 15)
        self._need(rc)
        return {"ok": rc == 0}

    # ---- diagnostics (read-only) -----------------------------------------------------------------------------------------
    def shell_readonly(self, org: str, host: str, serial: str, cmd: str) -> dict:
        self._serial(serial)
        if not read_only_shell(cmd):
            raise CoreError("not a read-only command; use “Request approval” for changes", 403)
        rc, out = self.run(org, host, ["-s", serial, "shell", *cmd.split()], 30)
        self._need(rc)
        return {"rc": rc, "output": out.decode(errors="replace")[:OUT_CAP]}

    def logcat(self, org: str, host: str, serial: str, lines: int = 300, level: str = "W") -> dict:
        self._serial(serial)
        if level not in "VDIWEF" or len(level) != 1:
            raise CoreError("level must be one of V D I W E F", 422)
        rc, out = self.run(org, host, ["-s", serial, "logcat", "-d", "-t", str(min(max(lines, 10), 2000)), f"*:{level}"], 30)
        self._need(rc)
        return {"output": out.decode(errors="replace")[-OUT_CAP:]}

    def packages(self, org: str, host: str, serial: str, system: bool = False) -> list[str]:
        self._serial(serial)
        rc, out = self.run(org, host, ["-s", serial, "shell", "pm", "list", "packages", "-s" if system else "-3"], 30)
        self._need(rc)
        return sorted(x.split(":", 1)[1].strip() for x in out.decode(errors="replace").splitlines() if x.startswith("package:"))

    def app(self, org: str, host: str, serial: str, action: str, pkg: str) -> dict:
        self._serial(serial)
        if not PKG.match(pkg):
            raise CoreError("invalid package name", 422)
        args = {"launch": ["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], "stop": ["am", "force-stop", pkg]}.get(action)
        if not args:
            raise CoreError("unknown action", 422)
        rc, out = self.run(org, host, ["-s", serial, "shell", *args], 20)
        self._need(rc)
        return {"ok": rc == 0, "output": out.decode(errors="replace")[:500]}

    # ---- changes that need a second person ---------------------------------------------------------------------------------
    def request(self, org: str, host: str, serial: str, kind: str, arg: str, actor: str, apk: bytes | None = None) -> dict:
        self._serial(serial)
        if kind not in ("install", "uninstall", "clear", "reboot", "shell"):
            raise CoreError("unknown change", 422)
        if kind in ("uninstall", "clear") and not PKG.match(arg):
            raise CoreError("invalid package name", 422)
        if kind == "shell" and (not arg.strip() or len(arg) > 300 or META.search(arg)):
            raise CoreError("shell commands may not contain ; & | < > $ ` or backslashes", 422)
        aid = new_id("and")
        if kind == "install":
            if not apk or apk[:2] != b"PK" or len(apk) > 60_000_000:
                raise CoreError("not a valid APK (max 60 MB)", 422)
            self.dir.mkdir(parents=True, exist_ok=True); (self.dir / f"{aid}.apk").write_bytes(apk); arg = arg[:80] or "app.apk"
        self.db.execute("INSERT INTO android_actions(id,org_id,host,serial,kind,arg,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (aid, org, host, serial, kind, arg, "pending_approval", actor, time.time()))
        self.db.audit(actor, "android.requested", None, org=org, host=host, serial=serial, kind=kind, request=aid)
        return self.action(aid)

    def action(self, aid: str) -> dict:
        return self.db.one("SELECT * FROM android_actions WHERE id=?", (aid,))

    def decide(self, org: str, aid: str, approver: str, approve: bool, is_owner: bool, same_person, confirm: str | None) -> dict:
        a = self.db.one("SELECT * FROM android_actions WHERE id=? AND org_id=?", (aid, org))
        if not a or a["status"] != "pending_approval":
            raise CoreError("unknown or already decided", 404)
        if not approve:
            self.db.execute("UPDATE android_actions SET status='rejected', approved_by=? WHERE id=?", (approver, aid)); self.db.audit(approver, "android.rejected", None, org=org, request=aid)
            return self.action(aid)
        if not is_owner and same_person(a["created_by"], approver):
            raise CoreError("a second person must approve (four-eyes rule)", 403)
        if a["kind"] == "reboot" and confirm != "I APPROVE":
            raise CoreError('rebooting needs the confirmation text "I APPROVE"', 422)
        self.db.execute("UPDATE android_actions SET status='running', approved_by=? WHERE id=?", (approver, aid))
        self.db.audit(approver, "android.approved", None, org=org, request=aid, kind=a["kind"], serial=a["serial"])
        s, h, ser = a["serial"], a["host"], a["serial"]
        try:
            if a["kind"] == "install":
                f = self.dir / f"{aid}.apk"
                if h == "main":
                    rc, out = self.run(org, h, ["-s", ser, "install", "-r", str(f)], 180)
                else:
                    cid = org_client(org)
                    self.call(h, "PUT", f"/clients/{cid}/fs/write", {"path": f"{aid}.apk", "content_b64": base64.b64encode(f.read_bytes()).decode()}, approver)
                    rc, out = self.run(org, h, ["-s", ser, "install", "-r", f"{aid}.apk"], 180)
            elif a["kind"] == "uninstall":
                rc, out = self.run(org, h, ["-s", ser, "uninstall", a["arg"]], 60)
            elif a["kind"] == "clear":
                rc, out = self.run(org, h, ["-s", ser, "shell", "pm", "clear", a["arg"]], 60)
            elif a["kind"] == "reboot":
                rc, out = self.run(org, h, ["-s", ser, "reboot"], 30)
            else:
                rc, out = self.run(org, h, ["-s", ser, "shell", *a["arg"].split()], 60)
            self._need(rc)
            res, st = out.decode(errors="replace")[:2000], ("done" if rc == 0 and b"Failure" not in out else "failed")
        except CoreError as e:
            res, st = str(e), "failed"
        self.db.execute("UPDATE android_actions SET status=?, result=? WHERE id=?", (st, res, aid))
        self.db.audit("system", "android.executed", None, org=org, request=aid, status=st)
        return self.action(aid)
