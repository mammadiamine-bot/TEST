"""Remote operations: built-in read-only diagnostic templates, versioned script library, multi-device jobs with approval,
retry/cancel, per-device history and schedules. Everything is executed through the Worker's existing authenticated API
(`/clients/{id}/exec`), so the Worker's workspace/permission/audit controls still apply; nothing here bypasses them.

Risk classes: read (no approval) | modify (administrator/owner approval) | disruptive (approval + typed confirmation, never schedulable).
Free-form commands are 'modify' unless an administrator has classified a stored script as 'read'.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .db import Db, new_id
from .service import CoreError

OUT_CAP = 64_000
SHELLS = ("cmd", "powershell")
RISKS = ("read", "modify", "disruptive")
NAME = re.compile(r"^[A-Za-z0-9 ._()-]{1,64}$")


def ps(script: str) -> dict:
    return {"shell": "powershell", "body": script}


def _json_ps(expr: str) -> str:
    return f"$ErrorActionPreference='Stop'; {expr} | ConvertTo-Json -Compress -Depth 4"


# id -> (title, risk, builder(params) -> {shell, body}, param validators)
TEMPLATES: dict[str, dict] = {
    "inventory.system": {"title": "System inventory (OS, CPU, RAM, uptime, disks, network)", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "[pscustomobject]@{host=$env:COMPUTERNAME; os=(Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,LastBootUpTime); "
        "cpu=(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors); ram_gb=[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1); "
        "disks=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,@{n='size_gb';e={[math]::Round($_.Size/1GB,1)}},@{n='free_gb';e={[math]::Round($_.FreeSpace/1GB,1)}}); "
        "net=@(Get-NetIPConfiguration | Where-Object IPv4Address | Select-Object InterfaceAlias,@{n='ipv4';e={$_.IPv4Address.IPAddress}})}"))},
    "inventory.apps": {"title": "Installed applications", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | "
        "Where-Object DisplayName | Select-Object DisplayName,DisplayVersion,Publisher | Sort-Object DisplayName"))},
    "services.list": {"title": "Windows services", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps("Get-Service | Select-Object Name,DisplayName,@{n='Status';e={$_.Status.ToString()}},@{n='StartType';e={$_.StartType.ToString()}}"))},
    "processes.top": {"title": "Top processes by memory", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 25 Id,ProcessName,@{n='mem_mb';e={[math]::Round($_.WorkingSet64/1MB,1)}},CPU"))},
    "eventlog.recent": {"title": "Recent errors/warnings from an event log", "risk": "read", "params": {"log": r"^(System|Application|Setup)$", "hours": r"^\d{1,3}$"}, "build": lambda p: ps(_json_ps(
        f"Get-WinEvent -FilterHashtable @{{LogName='{p.get('log', 'System')}'; Level=1,2,3; StartTime=(Get-Date).AddHours(-{int(p.get('hours', 24))})}} -MaxEvents 50 -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,@{n='msg';e={($_.Message -split \"`n\")[0]}}"))},
    "disk.usage": {"title": "Disk usage and cleanup candidates (no deletion)", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "[pscustomobject]@{temp_mb=[math]::Round((Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1MB,1); "
        "recycle_hint='Recycle Bin size is not measured'; downloads_mb=[math]::Round((Get-ChildItem \"$env:USERPROFILE\\Downloads\" -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1MB,1)}"))},
    "windows.update.status": {"title": "Windows Update status (read-only)", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "[pscustomobject]@{last_hotfixes=@(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID,InstalledOn,Description); "
        "reboot_pending=[bool](Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'); wuauserv=(Get-Service wuauserv).Status.ToString()}"))},
    "network.diag": {"title": "Network diagnostics (DNS, gateway, internet)", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "[pscustomobject]@{dns=@(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | Select-Object InterfaceAlias,ServerAddresses); "
        "gateway=@(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object NextHop,InterfaceAlias); internet=(Test-NetConnection 1.1.1.1 -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded; "
        "dns_resolves=[bool](Resolve-DnsName example.com -ErrorAction SilentlyContinue)}"))},
    "backup.status": {"title": "Backup status (File History / shadow copies / restore points)", "risk": "read", "params": {}, "build": lambda p: ps(_json_ps(
        "[pscustomobject]@{restore_points=@(Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Select-Object -Last 3 Description,CreationTime); "
        "shadow_copies=@(Get-CimInstance Win32_ShadowCopy -ErrorAction SilentlyContinue | Select-Object -Last 3 InstallDate); "
        "guidance='If none: enable System Protection or schedule a backup before any destructive change.'}"))},
    "service.restart": {"title": "Restart a Windows service", "risk": "modify", "params": {"name": r"^[A-Za-z0-9_.-]{1,64}$"}, "build": lambda p: ps(f"Restart-Service -Name '{p['name']}' -Force -ErrorAction Stop; (Get-Service '{p['name']}').Status.ToString()")},
    "service.stop": {"title": "Stop a Windows service", "risk": "modify", "params": {"name": r"^[A-Za-z0-9_.-]{1,64}$"}, "build": lambda p: ps(f"Stop-Service -Name '{p['name']}' -Force -ErrorAction Stop; (Get-Service '{p['name']}').Status.ToString()")},
    "system.reboot": {"title": "Reboot the computer (60 s warning)", "risk": "disruptive", "params": {}, "build": lambda p: {"shell": "cmd", "body": "shutdown /r /t 60 /c \"Restart requested by your IT provider\""}},
    "system.shutdown": {"title": "Shut the computer down (60 s warning)", "risk": "disruptive", "params": {}, "build": lambda p: {"shell": "cmd", "body": "shutdown /s /t 60 /c \"Shutdown requested by your IT provider\""}},
}
INVENTORY_KINDS = ("inventory.system", "inventory.apps", "services.list", "processes.top", "eventlog.recent", "windows.update.status", "disk.usage", "network.diag", "backup.status")


def render_template(tid: str, params: dict | None) -> dict:
    t = TEMPLATES.get(tid)
    if not t:
        raise CoreError("unknown template", 422)
    params = params or {}
    if set(params) - set(t["params"]):
        raise CoreError("unexpected template parameters", 422)
    for k, rx in t["params"].items():
        if k in params and not re.fullmatch(rx, str(params[k])):
            raise CoreError(f"invalid value for {k}", 422)
        if k not in params and tid.startswith("service."):
            raise CoreError(f"parameter {k} is required", 422)
    return {**t["build"]({k: str(v) for k, v in params.items()}), "risk": t["risk"], "title": t["title"]}


def argv_for(shell: str, body: str) -> list[str]:
    if shell == "cmd":
        return ["cmd.exe", "/d", "/c", body]
    if shell == "powershell":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", body]
    raise CoreError("shell must be cmd or powershell", 422)


def org_client(oid: str) -> str:
    return "org_" + oid.split("_")[-1].lower()[:30]  # one isolated workspace per client on every Worker


Caller = Callable[..., dict]


class Ops:
    def __init__(self, db: Db, call: Caller, retry_delay: float = 5.0, workers: int = 8):
        self.db, self.call, self.retry_delay = db, call, retry_delay
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cit-ops")
        self.cancelled: set[str] = set()
        self.ensured: set[tuple[str, str]] = set()
        self._stop = threading.Event()

    # ---- scripts (versioned)
    def create_script(self, org: str, name: str, shell: str, body: str, risk: str, author: str, can_classify: bool) -> dict:
        if not NAME.match(name) or shell not in SHELLS or risk not in RISKS or not body.strip() or len(body) > 20000:
            raise CoreError("invalid script (name, shell, risk or body)", 422)
        if risk == "read" and not can_classify:
            risk = "modify"  # only an administrator/owner may declare a free-form script read-only
        sid = new_id("scr")
        self.db.execute("INSERT INTO scripts(id,org_id,name,shell,risk,created_at) VALUES(?,?,?,?,?,?)", (sid, org, name, shell, risk, time.time()))
        self._add_version(sid, body, author)
        self.db.audit(author, "script.created", None, org=org, script=sid, risk=risk)
        return {"id": sid, "risk": risk, "version": 1}

    def _add_version(self, sid: str, body: str, author: str) -> int:
        import hashlib
        v = (self.db.one("SELECT COALESCE(MAX(version),0) m FROM script_versions WHERE script_id=?", (sid,))["m"]) + 1
        self.db.execute("INSERT INTO script_versions(script_id,version,body,sha256,author,created_at) VALUES(?,?,?,?,?,?)", (sid, v, body, hashlib.sha256(body.encode()).hexdigest(), author, time.time()))
        return v

    def new_version(self, org: str, sid: str, body: str, author: str, can_classify: bool) -> dict:
        s = self.db.one("SELECT * FROM scripts WHERE id=? AND org_id=?", (sid, org))
        if not s or not body.strip() or len(body) > 20000:
            raise CoreError("unknown script or invalid body", 404 if not s else 422)
        if s["risk"] == "read" and not can_classify:
            self.db.execute("UPDATE scripts SET risk='modify' WHERE id=?", (sid,))  # a technician's edit demotes it: it must be re-reviewed
        v = self._add_version(sid, body, author)
        self.db.audit(author, "script.version", None, org=org, script=sid, version=v)
        return {"id": sid, "version": v}

    # ---- jobs
    def create_job(self, org: str, actor: str, *, targets: list[str], title: str | None = None, shell: str | None = None, body: str | None = None, template: str | None = None,
                   params: dict | None = None, script_id: str | None = None, script_version: int | None = None, retries: int = 0, timeout: int = 300,
                   auto_approve: bool = False, from_schedule: str | None = None) -> dict:
        if not targets:
            raise CoreError("choose at least one machine", 422)
        for wid in targets:
            if not self.db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (wid, org)):
                raise CoreError("a target machine does not belong to this client", 422)  # dispatch can never cross clients
        sc_id = sc_v = None
        if template:
            t = render_template(template, params); shell, body, risk, title = t["shell"], t["body"], t["risk"], title or t["title"]
        elif script_id:
            s = self.db.one("SELECT * FROM scripts WHERE id=? AND org_id=?", (script_id, org))
            if not s:
                raise CoreError("unknown script", 404)
            v = self.db.one("SELECT * FROM script_versions WHERE script_id=? AND version=COALESCE(?,(SELECT MAX(version) FROM script_versions WHERE script_id=?))", (script_id, script_version, script_id))
            shell, body, risk, title, sc_id, sc_v = s["shell"], v["body"], s["risk"], title or s["name"], script_id, v["version"]
        else:
            if shell not in SHELLS or not (body or "").strip() or len(body) > 20000:
                raise CoreError("provide a template, a script, or shell+body", 422)
            risk, title = "modify", title or body.strip().splitlines()[0][:60]
        argv_for(shell, body)  # validates the shell
        status = "queued" if risk == "read" or auto_approve else "pending_approval"
        approval = "not_required" if risk == "read" else ("approved" if auto_approve else "pending")
        jid, now = new_id("job"), time.time()
        self.db.execute("INSERT INTO jobs(id,org_id,title,shell,body,risk,script_id,script_version,status,approval,approved_by,created_by,created_at,retries,timeout,schedule_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (jid, org, title[:120], shell, body, risk, sc_id, sc_v, status, approval, actor if auto_approve and risk != "read" else None, actor, now, min(max(retries, 0), 5), min(max(timeout, 5), 3600), from_schedule))
        for wid in dict.fromkeys(targets):
            self.db.execute("INSERT INTO job_runs(id,job_id,worker_id,status) VALUES(?,?,?,'pending')", (new_id("run"), jid, wid))
        self.db.audit(actor, "job.created", None, org=org, job=jid, risk=risk, targets=len(targets), title=title[:80])
        if status == "queued":
            self._start(jid)
        return self.job(jid)

    def _controls(self, creator: str, approver: str) -> bool:
        """True when the approver is the creator OR minted/invited/last rotated the creator's token: they could act as the creator, so that is not a second person."""
        if creator == approver:
            return True
        p = self.db.one("SELECT created_by, rotated_by FROM principals WHERE id=?", (creator,))
        return bool(p) and approver in (p["created_by"], p["rotated_by"])

    def approve(self, org: str, jid: str, approver: str, is_owner: bool, confirm: str | None) -> dict:
        j = self._job_of(org, jid)
        if j["status"] != "pending_approval":
            raise CoreError("this job is not waiting for approval", 409)
        if not is_owner and self._controls(j["created_by"], approver):
            raise CoreError("a second person must approve (four-eyes rule)", 403)
        if j["risk"] == "disruptive" and confirm != "I APPROVE":
            raise CoreError('disruptive jobs need the confirmation text "I APPROVE"', 422)
        self.db.execute("UPDATE jobs SET approval='approved', approved_by=?, status='queued' WHERE id=?", (approver, jid))
        self.db.audit(approver, "job.approved", None, org=org, job=jid, risk=j["risk"])
        self._start(jid)
        return self.job(jid)

    def reject(self, org: str, jid: str, actor: str) -> dict:
        j = self._job_of(org, jid)
        if j["status"] != "pending_approval":
            raise CoreError("this job is not waiting for approval", 409)
        self.db.execute("UPDATE jobs SET approval='rejected', status='cancelled' WHERE id=?", (jid,))
        self.db.execute("UPDATE job_runs SET status='cancelled' WHERE job_id=?", (jid,))
        self.db.audit(actor, "job.rejected", None, org=org, job=jid)
        return self.job(jid)

    def cancel(self, org: str, jid: str, actor: str) -> dict:
        j = self._job_of(org, jid)
        self.cancelled.add(jid)
        for r in self.db.all("SELECT * FROM job_runs WHERE job_id=? AND status IN ('pending','running')", (jid,)):
            if r["remote"]:
                try:
                    self.call(r["worker_id"], "POST", f"/clients/{org_client(org)}/jobs/{r['remote']}/cancel", None, actor)
                except CoreError:
                    pass
            elif r["status"] == "pending":
                self.db.execute("UPDATE job_runs SET status='cancelled', finished_at=? WHERE id=?", (time.time(), r["id"]))
        if j["status"] in ("pending_approval", "queued"):
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (jid,))
        self.db.audit(actor, "job.cancel_requested", None, org=org, job=jid)
        return self.job(jid)

    def _job_of(self, org: str, jid: str) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (jid, org))
        if not j:
            raise CoreError("unknown job", 404)
        return j

    def job(self, jid: str) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        return {**j, "runs": self.db.all("SELECT id,worker_id,status,attempt,exit_code,output,started_at,finished_at,error FROM job_runs WHERE job_id=?", (jid,))}

    # ---- execution
    def _start(self, jid: str) -> None:
        self.db.execute("UPDATE jobs SET status='running' WHERE id=? AND status IN ('queued')", (jid,))
        for r in self.db.all("SELECT id FROM job_runs WHERE job_id=? AND status='pending'", (jid,)):
            self.pool.submit(self._run, jid, r["id"])

    def _ensure_client(self, wid: str, org: str) -> str:
        cid = org_client(org)
        if (wid, cid) not in self.ensured:
            try:
                self.call(wid, "POST", "/clients", {"id": cid, "name": f"CIT client {cid}"}, "system")
            except CoreError as e:
                if e.status != 409:  # 409 = already exists
                    raise
            self.ensured.add((wid, cid))
        return cid

    def _run(self, jid: str, run_id: str) -> None:
        j, r = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,)), self.db.one("SELECT * FROM job_runs WHERE id=?", (run_id,))
        out, err, status, code = "", None, "failed", None
        for attempt in range(j["retries"] + 1):
            if jid in self.cancelled:
                status = "cancelled"; break
            self.db.execute("UPDATE job_runs SET status='running', attempt=?, started_at=COALESCE(started_at,?) WHERE id=?", (attempt + 1, time.time(), run_id))
            try:
                cid = self._ensure_client(r["worker_id"], j["org_id"])
                rem = self.call(r["worker_id"], "POST", f"/clients/{cid}/exec", {"argv": argv_for(j["shell"], j["body"]), "timeout": j["timeout"]}, j["created_by"])["job"]
                self.db.execute("UPDATE job_runs SET remote=? WHERE id=?", (rem, run_id))
                since, deadline, out = 0, time.time() + j["timeout"] + 30, ""
                while True:
                    s = self.call(r["worker_id"], "GET", f"/clients/{cid}/jobs/{rem}?since={since}", None, "system")
                    out = (out + (s.get("output") or ""))[-OUT_CAP:]; since = s["next"]
                    if s["done"]:
                        code = s.get("exit")
                        status = "cancelled" if s.get("cancelled") else ("timed_out" if s.get("timed_out") else ("ok" if code == 0 else "failed")); err = None
                        break
                    if time.time() > deadline:
                        status, err = "timed_out", "no completion before the deadline"; break
                    time.sleep(0.4 if not self.retry_delay < 0.2 else 0.02)
                break
            except CoreError as e:  # transport/availability problems are retried; command failures are not
                status, err = "unreachable", str(e)[:200]
                if attempt < j["retries"] and jid not in self.cancelled:
                    time.sleep(self.retry_delay)
        self.db.execute("UPDATE job_runs SET status=?, exit_code=?, output=?, error=?, finished_at=? WHERE id=?", (status, code, out, err, time.time(), run_id))
        self.db.audit("system", "job.run_finished", None, job=jid, worker=r["worker_id"], status=status, exit=code)
        self._finish(jid)

    def _finish(self, jid: str) -> None:
        runs = self.db.all("SELECT status FROM job_runs WHERE job_id=?", (jid,))
        if any(x["status"] in ("pending", "running") for x in runs):
            return
        st = [x["status"] for x in runs]
        final = "cancelled" if jid in self.cancelled else ("completed" if all(s == "ok" for s in st) else ("failed" if not any(s == "ok" for s in st) else "partial"))
        self.db.execute("UPDATE jobs SET status=? WHERE id=?", (final, jid))

    # ---- inventory (read-only templates, latest result kept)
    def collect(self, org: str, wid: str, kind: str, actor: str, params: dict | None = None, wait: float = 90.0) -> dict:
        if kind not in INVENTORY_KINDS:
            raise CoreError("unknown inventory kind", 422)
        j = self.create_job(org, actor, targets=[wid], template=kind, params=params, timeout=int(wait))
        end = time.time() + wait + 5
        while time.time() < end:
            cur = self.job(j["id"])
            if cur["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        run = self.job(j["id"])["runs"][0]
        data: object = run["output"]
        try:
            data = json.loads(run["output"])
        except (ValueError, TypeError):
            pass
        if run["status"] == "ok":
            self.db.execute("INSERT OR REPLACE INTO inventory(worker_id,kind,ts,data) VALUES(?,?,?,?)", (wid, kind, time.time(), json.dumps(data)))
        return {"status": run["status"], "error": run["error"], "data": data, "job": j["id"]}

    def latest(self, wid: str, kind: str) -> dict | None:
        r = self.db.one("SELECT ts,data FROM inventory WHERE worker_id=? AND kind=?", (wid, kind))
        return {"ts": r["ts"], "data": json.loads(r["data"])} if r else None

    # ---- schedules (read: automatic; modify: pre-approved by an administrator at creation; disruptive: never scheduled)
    def create_schedule(self, org: str, actor: str, *, title: str, targets: list[str], every_minutes: int, template: str | None, params: dict | None, shell: str | None, body: str | None) -> dict:
        if every_minutes < 5 or every_minutes > 60 * 24 * 31:
            raise CoreError("interval must be between 5 minutes and 31 days", 422)
        if template:
            t = render_template(template, params); shell, body, risk = t["shell"], t["body"], t["risk"]
        else:
            argv_for(shell or "", body or ""); risk = "modify"
        if risk == "disruptive":
            raise CoreError("disruptive operations cannot be scheduled; each run needs a human approval", 422)
        for wid in targets:
            if not self.db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (wid, org)):
                raise CoreError("a target machine does not belong to this client", 422)
        sid, now = new_id("sch"), time.time()
        enabled = 1 if risk == "read" else 0  # read-only diagnostics run unattended; anything that changes a machine waits for a second person
        self.db.execute("INSERT INTO schedules(id,org_id,title,shell,body,risk,targets,every_minutes,enabled,next_run,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, org, title[:120], shell, body, risk, json.dumps(targets), every_minutes, enabled, now + every_minutes * 60, actor, now))
        self.db.audit(actor, "schedule.created", None, org=org, schedule=sid, risk=risk, every_minutes=every_minutes, enabled=bool(enabled))
        return {"id": sid, "risk": risk, "enabled": bool(enabled), "next_run": now + every_minutes * 60}

    def approve_schedule(self, org: str, sid: str, approver: str, is_owner: bool) -> dict:
        s = self.db.one("SELECT * FROM schedules WHERE id=? AND org_id=?", (sid, org))
        if not s:
            raise CoreError("unknown schedule", 404)
        if not is_owner and self._controls(s["created_by"], approver):
            raise CoreError("a second person must approve (four-eyes rule)", 403)
        self.db.execute("UPDATE schedules SET approved_by=?, enabled=1 WHERE id=?", (approver, sid))
        self.db.audit(approver, "schedule.approved", None, org=org, schedule=sid, risk=s["risk"])
        return {"id": sid, "enabled": True, "approved_by": approver}

    def tick(self, now: float | None = None) -> int:
        now, n = now or time.time(), 0
        for s in self.db.all("SELECT * FROM schedules WHERE enabled=1 AND next_run<=? AND (risk='read' OR approved_by IS NOT NULL)", (now,)):
            self.db.execute("UPDATE schedules SET last_run=?, next_run=? WHERE id=?", (now, now + s["every_minutes"] * 60, s["id"]))
            live = [w for w in json.loads(s["targets"]) if self.db.one("SELECT 1 AS x FROM workers WHERE id=? AND org_id=?", (w, s["org_id"]))]
            org = self.db.one("SELECT status FROM orgs WHERE id=?", (s["org_id"],))
            if live and org and (org["status"] or "active") == "active":
                self.create_job(s["org_id"], "schedule:" + s["id"], targets=live, title=s["title"], shell=s["shell"], body=s["body"], auto_approve=True, from_schedule=s["id"]); n += 1
        return n

    def start_scheduler(self, every: float = 20.0) -> None:
        def loop():
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True).start()
