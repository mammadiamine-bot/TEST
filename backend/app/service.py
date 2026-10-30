"""Core orchestration: authorization, sessions, diagnostics, repair plans, approvals, verification."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import diagnostics as dx
from . import tools
from .config import Settings
from .db import Db, new_id
from .netpolicy import NetworkPolicyError, assert_private_address
from .security import SecretProtector, default_protector
from .tools import Risk, ToolError
from .transports.base import Transport, TransportError
from .targets import LocalTarget
from .targetcore import Grants, TargetError
from .transports.agent import AgentClient, norm_fp
from .transports.mock import SCENARIOS, MockTransport


class CoreError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def ssh_fingerprint(known_hosts_line: str) -> str:
    parts = known_hosts_line.split()
    if len(parts) < 3:
        raise CoreError("host key must be a known_hosts line: '<host> <type> <base64>'")
    blob = base64.b64decode(parts[2])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def step_hash(device_id: str, tool_id: str, params: dict) -> str:
    return _h(json.dumps([device_id, tool_id, params], sort_keys=True))


class Core:
    def __init__(self, settings: Settings, db: Db | None = None, protector: SecretProtector | None = None,
                 cloud_probe: Callable[[], bool] | None = None, now: Callable[[], float] = time.time):
        self.s = settings
        self.db = db or Db(settings.db_path)
        self.protector = protector or default_protector()
        self.cloud_probe = cloud_probe or self._probe
        self.now = now
        self.transports: dict[str, Transport] = {}
        self._attempts: dict[str, int] = {}
        self._dev_locks: dict[str, threading.Lock] = {}
        self.stop_hooks: list = []
        self._local = None

    # ---------------------------------------------------------------- connectivity
    def _probe(self) -> bool:
        try:
            socket.create_connection((self.s.cloud_probe_host, 443), timeout=2).close()
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------- devices & pairing
    def create_device(self, name, address, transport, owner_name, owner_contact, authorization_note, scenario=None, actor="technician",
                      kind="workstation", allow_public=False) -> dict:
        if transport not in ("mock", "ssh", "winrm", "agent"):
            raise CoreError("transport must be mock, ssh, winrm or agent")
        if kind not in ("workstation", "server"):
            raise CoreError("kind must be workstation or server")
        if transport == "mock":
            if not self.s.allow_mock:
                raise CoreError("mock devices are disabled")
            scenario = scenario or "broken"
            if scenario not in SCENARIOS:
                raise CoreError("unknown scenario")
        elif allow_public:
            # Outbound key-only SSH to a VPS you operate. Never allowed for the agent (it only accepts private sources) or WinRM.
            if not (transport == "ssh" and kind == "server"):
                raise CoreError("public addresses are only allowed for SSH servers (VPS)", 422)
            if len(authorization_note) < 20:
                raise CoreError("describe your authorization for this public server (20+ characters)", 422)
            self.db.audit(actor, "device.public_server_ack", None, address=address)
        else:
            try:
                assert_private_address(address.rsplit(":", 1)[0] if transport == "agent" and ":" in address else address)
            except NetworkPolicyError as e:
                self.db.audit(actor, "device.rejected", None, address=address, reason=str(e))
                raise CoreError(str(e), 422)
        did, code = new_id("dev"), f"{secrets.randbelow(10**6):06d}"
        self.db.execute(
            "INSERT INTO devices(id,name,address,transport,owner_name,owner_contact,authorization_note,status,pairing_hash,pairing_expires,scenario,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, name, address, transport, owner_name, owner_contact, authorization_note, "pending_pairing",
             _h(code) if transport != "agent" else None, self.now() + self.s.pairing_ttl_seconds, scenario, self.now()))
        self.db.execute("UPDATE devices SET scenario=? WHERE id=?", (f"kind={kind}" if transport != "mock" else scenario, did))
        self.db.audit(actor, "device.create", did, name=name, transport=transport, owner=owner_name)
        d = self.device(did)
        d["kind"] = kind
        if transport != "agent":  # agent devices get their code from the CLIENT's screen, never from us
            d["pairing_code"] = code
        return d

    def device(self, did: str) -> dict:
        d = self.db.one("SELECT * FROM devices WHERE id=?", (did,))
        if not d:
            raise CoreError("device not found", 404)
        d.pop("pairing_hash", None)
        d["kind"] = "server" if (d.get("scenario") or "") == "kind=server" else ("control" if d["transport"] == "local" else "workstation")
        d["live"] = d["id"] in self.transports or d["transport"] == "local"
        return d

    def devices(self) -> list[dict]:
        return [self.device(r["id"]) for r in self.db.all("SELECT id FROM devices ORDER BY created_at")]

    def pair(self, did: str, code: str, fingerprint: str, host_key: str | None = None, actor="technician") -> dict:
        row = self.db.one("SELECT * FROM devices WHERE id=?", (did,))
        if not row:
            raise CoreError("device not found", 404)
        if row["transport"] == "agent":
            return self.connect_agent(did, code, fingerprint, actor)
        if row["status"] != "pending_pairing":
            raise CoreError("device is not awaiting pairing", 409)
        self._throttle(f"pair:{did}")
        if self.now() > row["pairing_expires"]:
            raise CoreError("pairing code expired", 410)
        if not secrets.compare_digest(_h(code), row["pairing_hash"]):
            self.db.audit(actor, "device.pair_failed", did)
            raise CoreError("wrong pairing code", 403)
        if row["transport"] == "ssh":
            if not host_key or ssh_fingerprint(host_key) != fingerprint:
                self.db.audit(actor, "device.pair_failed", did, reason="fingerprint mismatch")
                raise CoreError("host key does not match the fingerprint confirmed by the owner", 403)
        if row["transport"] == "mock":
            fingerprint = self._mock(did).host_fingerprint()
        self.db.execute("UPDATE devices SET status='paired',fingerprint=?,pairing_hash=NULL WHERE id=?", (fingerprint, did))
        if host_key:
            kh = self.s.data_dir / "known_hosts" / f"{did}"
            kh.parent.mkdir(parents=True, exist_ok=True)
            kh.write_text(host_key.strip() + "\n")
        self.db.audit(actor, "device.paired", did, fingerprint=fingerprint)
        return self.device(did)

    # ---------------------------------------------------------------- portable agent (consent happens on the CLIENT's screen)
    def connect_agent(self, did: str, code: str, fingerprint: str, actor="technician") -> dict:
        row = self.db.one("SELECT * FROM devices WHERE id=?", (did,))
        if not row or row["transport"] != "agent":
            raise CoreError("not an agent device", 404)
        if row["status"] == "revoked":
            raise CoreError("device was revoked", 403)
        host, _, port = row["address"].partition(":")
        if not fingerprint or len(norm_fp(fingerprint)) != 64:
            raise CoreError("enter the fingerprint shown on the client's screen (64 hex characters)", 422)
        self._throttle(f"agent:{did}", 8)
        client = AgentClient(host, int(port or 8766), fingerprint)
        try:
            res = client.pair(code, "technician-station")
        except TargetError as e:
            self.db.audit(actor, "agent.pair_failed", did, reason=str(e)); raise CoreError(str(e), e.status)
        except Exception as e:  # noqa: BLE001  (TransportError: unreachable / identity mismatch)
            self.db.audit(actor, "agent.pair_failed", did, reason=str(e)); raise CoreError(str(e), 502)
        self.transports[did] = client
        scope = "repair" if set(res["grants"]) & {"repair", "write", "terminal", "claude"} else "diagnose"  # any state-changing grant
        t = self.now()
        for s_ in self.db.all("SELECT id FROM sessions WHERE device_id=? AND status IN ('requested','active')", (did,)):
            self.db.execute("UPDATE sessions SET status='revoked',ended_at=? WHERE id=?", (t, s_["id"]))
        sid = new_id("ses")
        mins = max(1, int((res["expires_at"] - time.time()) / 60))
        self.db.execute("INSERT INTO sessions(id,device_id,scope,status,requested_by,consenting_person,duration_minutes,created_at,activated_at,expires_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, did, scope, "active", actor, "client (agent console)", mins, t, t, t + (res["expires_at"] - time.time())))
        self.db.execute("UPDATE devices SET status='paired',fingerprint=? WHERE id=?", (norm_fp(fingerprint), did))
        self.db.audit(actor, "agent.paired", did, fingerprint=norm_fp(fingerprint), grants=res["grants"], session=sid)
        return self.device(did)

    def agent(self, did: str) -> AgentClient:
        c = self.transports.get(did)
        if not isinstance(c, AgentClient):
            raise CoreError("agent session is not connected — ask the client to start the agent and pair again", 409)
        return c

    # ---------------------------------------------------------------- targets (machine + project directories)
    def local_target(self) -> LocalTarget:
        if getattr(self, "_local", None) is None:
            roots = [Path(r) for r in self.s.local_roots] or [self.s.data_dir / "workspaces"]
            for r in roots:
                r.mkdir(parents=True, exist_ok=True)
            self._local = LocalTarget(roots, Grants(terminal=True, write=True, claude=True))
        return self._local

    def ensure_local_device(self) -> dict:
        r = self.db.one("SELECT id FROM devices WHERE transport='local'")
        if r:
            return self.device(r["id"])
        did = new_id("dev")
        self.db.execute("INSERT INTO devices(id,name,address,transport,owner_name,owner_contact,authorization_note,status,scenario,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)", (did, "This computer (control station)", "localhost", "local", "Technician", "-",
                                                         "The technician's own computer", "paired", None, self.now()))
        self.db.audit("system", "device.local_registered", did)
        return self.device(did)

    def target(self, did: str, need_session: str = "diagnose"):
        """The only way dev/server/task code reaches a machine's filesystem or shell."""
        d = self.device(did)
        if d["transport"] == "local":
            return self.local_target()
        self._require(did, need_session)
        if d["transport"] == "agent":
            return self.agent(did)
        if d["transport"] == "ssh" and d["kind"] == "server":
            return self.ssh_target(d)
        raise CoreError(f"{d['transport']} devices have no project/terminal access (use the agent or SSH to a Linux server)", 501)

    def ssh_target(self, d: dict):
        from .servers import LinuxSshTarget
        row = self.db.one("SELECT blob FROM credentials WHERE device_id=?", (d["id"],))
        if not row:
            raise CoreError("no stored SSH credentials for this server (needs Windows DPAPI)", 409)
        c = json.loads(self.protector.unprotect(row["blob"]))
        host, _, port = d["address"].partition(":")
        key = (self.s.data_dir / "known_hosts" / d["id"])
        return LinuxSshTarget(host, c["user"], c["key_path"], key, int(port or 22), self.s.ssh_bin)

    def emergency_stop(self, actor="technician") -> dict:
        """Kill switch: end every session, disconnect every agent, cancel every job."""
        n_s = 0
        for s_ in self.db.all("SELECT id,device_id FROM sessions WHERE status IN ('requested','active')"):
            self.db.execute("UPDATE sessions SET status='revoked',ended_at=? WHERE id=?", (self.now(), s_["id"])); n_s += 1
        n_t = 0
        for did, tr in list(self.transports.items()):
            if hasattr(tr, "disconnect"):
                try:
                    tr.disconnect(); n_t += 1
                except Exception:  # noqa: BLE001
                    pass
            self.transports.pop(did, None)
        if getattr(self, "_local", None):
            self._local.disconnect()
        for hook in self.stop_hooks:
            hook()
        self.db.audit(actor, "emergency.stop", None, sessions=n_s, disconnected=n_t)
        return {"sessions_revoked": n_s, "targets_disconnected": n_t}

    def set_credentials(self, did: str, creds: dict) -> None:
        self.device(did)
        blob = self.protector.protect(json.dumps(creds).encode())
        self.db.execute("INSERT OR REPLACE INTO credentials(device_id,blob) VALUES(?,?)", (did, blob))
        self.db.audit("technician", "credentials.stored", did)  # never log the secret

    def revoke_device(self, did: str, actor="technician") -> dict:
        self.device(did)
        self.db.execute("UPDATE devices SET status='revoked',revoked_at=? WHERE id=?", (self.now(), did))
        self.db.execute("UPDATE sessions SET status='revoked',ended_at=? WHERE device_id=? AND status IN ('requested','active')", (self.now(), did))
        self.db.execute("DELETE FROM credentials WHERE device_id=?", (did,))
        tr = self.transports.pop(did, None)
        if tr is not None and hasattr(tr, "disconnect"):
            tr.disconnect()
        self.db.audit(actor, "device.revoked", did)
        return self.device(did)

    def _throttle(self, key: str, limit: int = 5) -> None:
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if self._attempts[key] > limit:
            raise CoreError("too many attempts", 429)

    # ---------------------------------------------------------------- consent sessions
    def request_session(self, did: str, scope: str, minutes: int, actor="technician") -> dict:
        d = self.device(did)
        if d["status"] != "paired":
            raise CoreError("device is not paired/authorized", 403)
        if scope not in ("diagnose", "repair"):
            raise CoreError("scope must be diagnose or repair")
        if not 1 <= minutes <= self.s.max_session_minutes:
            raise CoreError(f"duration must be 1..{self.s.max_session_minutes} minutes")
        sid, code = new_id("ses"), f"{secrets.randbelow(10**6):06d}"
        self.db.execute("INSERT INTO sessions(id,device_id,scope,status,requested_by,consent_hash,duration_minutes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (sid, did, scope, "requested", actor, _h(code), minutes, self.now()))
        self.db.audit(actor, "session.requested", did, session=sid, scope=scope, minutes=minutes)
        s = self.session(sid)
        s["consent_code"] = code  # delivered to the client; only the client's confirmation activates the session
        return s

    def session(self, sid: str) -> dict:
        s = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not s:
            raise CoreError("session not found", 404)
        if s["status"] == "active" and self.now() > s["expires_at"]:
            self.db.execute("UPDATE sessions SET status='expired',ended_at=? WHERE id=?", (s["expires_at"], sid))
            self.db.audit("system", "session.expired", s["device_id"], session=sid)
            s["status"] = "expired"
        s.pop("consent_hash", None)
        return s

    def sessions(self, did: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT id FROM sessions" + (" WHERE device_id=?" if did else "") + " ORDER BY created_at DESC", (did,) if did else ())
        return [self.session(r["id"]) for r in rows]

    def consent(self, sid: str, code: str, person: str) -> dict:
        row = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not row:
            raise CoreError("session not found", 404)
        if row["status"] != "requested":
            raise CoreError("session is not awaiting consent", 409)
        self._throttle(f"consent:{sid}")
        if not secrets.compare_digest(_h(code), row["consent_hash"]):
            self.db.audit(person, "session.consent_failed", row["device_id"], session=sid)
            raise CoreError("wrong consent code", 403)
        if not person.strip():
            raise CoreError("consenting person is required")
        t = self.now()
        self.db.execute("UPDATE sessions SET status='active',consenting_person=?,activated_at=?,expires_at=?,consent_hash=NULL WHERE id=?",
                        (person, t, t + row["duration_minutes"] * 60, sid))
        self.db.audit(person, "session.consented", row["device_id"], session=sid, scope=row["scope"])
        return self.session(sid)

    def end_session(self, sid: str, actor: str) -> dict:
        s = self.session(sid)
        if s["status"] in ("requested", "active"):
            self.db.execute("UPDATE sessions SET status='revoked',ended_at=? WHERE id=?", (self.now(), sid))
            tr = self.transports.get(s["device_id"])
            if isinstance(tr, AgentClient):
                tr.disconnect(); self.transports.pop(s["device_id"], None)
            self.db.audit(actor, "session.revoked", s["device_id"], session=sid)
        return self.session(sid)

    def _require(self, did: str, need: str) -> dict:
        d = self.device(did)
        if d["status"] != "paired":
            raise CoreError("device is not authorized", 403)
        for s in self.sessions(did):
            if s["status"] == "active" and (need == "diagnose" or s["scope"] == "repair"):
                return s
        self.db.audit("system", "access.denied", did, need=need)
        raise CoreError(f"no active client-consented session with '{need}' scope", 403)

    # ---------------------------------------------------------------- transports
    def _mock(self, did: str) -> MockTransport:
        if did not in self.transports:
            d = self.db.one("SELECT scenario FROM devices WHERE id=?", (did,))
            self.transports[did] = MockTransport(d["scenario"] or "broken")
        return self.transports[did]  # type: ignore[return-value]

    def _transport(self, d: dict) -> Transport:
        if d["transport"] == "mock":
            return self._mock(d["id"])
        if d["id"] in self.transports:
            return self.transports[d["id"]]
        row = self.db.one("SELECT blob FROM credentials WHERE device_id=?", (d["id"],))
        if not row:
            raise CoreError("no stored credentials for this device", 409)
        c = json.loads(self.protector.unprotect(row["blob"]))
        try:
            if d["transport"] == "ssh":
                from .transports.ssh import SshTransport
                t: Transport = SshTransport(d["address"], c["user"], c["key_path"], self.s.data_dir / "known_hosts" / d["id"], d["fingerprint"])
            else:
                from .transports.winrm import WinRmTransport
                t = WinRmTransport(d["address"], c["user"], c["password"], c.get("ca_trust"), d["fingerprint"])
        except (NetworkPolicyError, TransportError) as e:
            raise CoreError(str(e), 502)
        self.transports[d["id"]] = t
        return t

    def _exec(self, d: dict, tool_id: str, params: dict | None, actor: str) -> tuple[Any, tools.Tool, dict]:
        """The single choke point for touching a device."""
        tool = tools.get(tool_id)
        clean = tool.validate(params)
        tr = self._transport(d)
        if tr.host_fingerprint() != d["fingerprint"]:
            self.db.audit("system", "identity.mismatch", d["id"], tool=tool_id)
            raise CoreError("device identity mismatch: host fingerprint changed since pairing", 403)
        r = tr.run(tool, clean, tool.timeout_s)
        self.db.audit(actor, "tool.run", d["id"], tool=tool_id, risk=tool.risk.value, params=clean, ok=r.ok, ms=r.duration_ms, error=r.error[:200])
        if not r.ok:
            raise CoreError(f"{tool_id} failed: {r.error or 'device error'}", 502)
        return r.output, tool, clean

    # ---------------------------------------------------------------- diagnostics
    def diagnose(self, did: str, modules: list[str] | None = None, actor="technician") -> dict:
        self._require(did, "diagnose")
        d = self.device(did)
        modules = modules or dx.MODULES
        bad = [m for m in modules if m not in dx.ANALYZERS]
        if bad:
            raise CoreError(f"unknown modules: {bad}")
        data: dict[str, Any] = {}
        errors: dict[str, str] = {}
        findings: list[dict] = []
        for m in modules:
            try:
                out, _, _ = self._exec(d, m, {}, actor)
                data[m] = dx.parse(out)
                findings += dx.analyze(m, data[m])
            except (CoreError, ValueError, KeyError, ToolError, TransportError) as e:
                errors[m] = str(e)
        findings.sort(key=lambda f: dx.SEV_ORDER[f["severity"]])
        run = {"id": new_id("run"), "device_id": did, "created_at": self.now(), "modules": modules,
               "data": data, "errors": errors, "findings": findings, "source": "scripted"}
        self.db.execute("INSERT INTO runs(id,device_id,created_at,data) VALUES(?,?,?,?)", (run["id"], did, run["created_at"], json.dumps(run)))
        self.db.audit(actor, "diagnose.run", did, run=run["id"], modules=len(modules), findings=len(findings), errors=len(errors))
        return run

    def run(self, rid: str) -> dict:
        r = self.db.one("SELECT data FROM runs WHERE id=?", (rid,))
        if not r:
            raise CoreError("run not found", 404)
        return json.loads(r["data"])

    def runs(self, did: str) -> list[dict]:
        return [{k: v for k, v in json.loads(r["data"]).items() if k != "data"} for r in
                self.db.all("SELECT data FROM runs WHERE device_id=? ORDER BY created_at DESC", (did,))]

    def metrics(self, did: str) -> dict:
        self._require(did, "diagnose")
        d = self.device(did)
        out, _, _ = self._exec(d, "cpu_ram", {}, "system")
        return {"ts": self.now(), **dx.parse(out)}

    def console(self, did: str, command: str, actor="technician") -> dict:
        self._require(did, "diagnose")
        d = self.device(did)
        try:
            tools.validate_console(command)
        except ToolError as e:
            self.db.audit(actor, "console.rejected", did, command=command[:200])
            raise CoreError(str(e), 422)
        r = self._transport(d).run_console(command, 30)
        self.db.audit(actor, "console.run", did, command=command, ok=r.ok)
        return {"ok": r.ok, "output": r.output[:20000], "error": r.error}

    # ---------------------------------------------------------------- repair plans
    def create_plan(self, did: str, rid: str) -> dict:
        self._require(did, "diagnose")
        run = self.run(rid)
        if run["device_id"] != did:
            raise CoreError("run belongs to another device", 403)
        steps, seen = [], set()
        for f in run["findings"]:
            for sug in f["suggested"]:
                tool = tools.get(sug["tool_id"])
                key = (tool.id, json.dumps(sug["params"], sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                steps.append({"id": f"s{len(steps)+1}", "finding_id": f["id"], "tool_id": tool.id, "title": tool.title,
                              "params": tool.validate(sug["params"]), "risk": tool.risk.value, "severity": f["severity"],
                              "rationale": f["title"], "status": "proposed", "approved_hash": None, "approved_by": None,
                              "result": None})
        plan = {"id": new_id("plan"), "device_id": did, "run_id": rid, "status": "proposed", "created_at": self.now(),
                "steps": steps, "before_findings": run["findings"], "after": None}
        self._save_plan(plan)
        self.db.audit("technician", "plan.created", did, plan=plan["id"], steps=len(steps))
        return plan

    def _save_plan(self, p: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO plans(id,device_id,run_id,status,created_at,data) VALUES(?,?,?,?,?,?)",
                        (p["id"], p["device_id"], p["run_id"], p["status"], p["created_at"], json.dumps(p)))

    def plan(self, pid: str) -> dict:
        r = self.db.one("SELECT data FROM plans WHERE id=?", (pid,))
        if not r:
            raise CoreError("plan not found", 404)
        return json.loads(r["data"])

    def plans(self, did: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self.db.all("SELECT data FROM plans WHERE device_id=? ORDER BY created_at DESC", (did,))]

    def approve_step(self, pid: str, sid: str, approver: str, confirm_high_risk: bool = False) -> dict:
        p = self.plan(pid)
        st = next((s for s in p["steps"] if s["id"] == sid), None)
        if not st:
            raise CoreError("step not found", 404)
        if st["status"] != "proposed":
            raise CoreError(f"step is {st['status']}", 409)
        if st["risk"] == Risk.HIGH.value and not confirm_high_risk:
            raise CoreError("high-risk step needs explicit per-action confirmation", 428)
        st.update(status="approved", approved_by=approver, approved_hash=step_hash(p["device_id"], st["tool_id"], st["params"]))
        p["status"] = "approved"
        self._save_plan(p)
        self.db.audit(approver, "plan.step_approved", p["device_id"], plan=pid, step=sid, tool=st["tool_id"], risk=st["risk"])
        return p

    def approve_low_risk(self, pid: str, approver: str) -> dict:
        p = self.plan(pid)
        for st in p["steps"]:
            if st["status"] == "proposed" and st["risk"] == Risk.LOW.value:
                p = self.approve_step(pid, st["id"], approver)
        return p

    def reject_step(self, pid: str, sid: str, actor: str) -> dict:
        p = self.plan(pid)
        for st in p["steps"]:
            if st["id"] == sid and st["status"] in ("proposed", "approved"):
                st["status"] = "rejected"
        self._save_plan(p)
        self.db.audit(actor, "plan.step_rejected", p["device_id"], plan=pid, step=sid)
        return p

    def execute_plan(self, pid: str, actor="technician") -> dict:
        p = self.plan(pid)
        did = p["device_id"]
        lock = self._dev_locks.setdefault(did, threading.Lock())
        if not lock.acquire(blocking=False):
            raise CoreError("another operation is running on this device", 409)
        try:
            todo = [s for s in p["steps"] if s["status"] == "approved"]
            if not todo:
                raise CoreError("no approved steps to execute", 409)
            self._require(did, "repair")
            d = self.device(did)
            for st in todo:
                self._run_step(d, p, st, actor)
                self._save_plan(p)
            ran = [s for s in p["steps"] if s["status"] in ("verified", "unverified", "failed", "blocked")]
            p["status"] = "completed" if all(s["status"] == "verified" for s in ran) else "completed_with_issues"
            after = self.diagnose(did, [m for m in self.run(p["run_id"])["modules"]], actor)
            p["after"] = {"run_id": after["id"], "findings": after["findings"]}
            self._save_plan(p)
            self.db.audit(actor, "plan.executed", did, plan=pid, status=p["status"])
            return p
        finally:
            lock.release()

    def _run_step(self, d: dict, p: dict, st: dict, actor: str) -> None:
        tool = tools.get(st["tool_id"])
        try:
            self._require(d["id"], "repair")  # session may have expired / been revoked mid-plan
            if step_hash(d["id"], st["tool_id"], st["params"]) != st["approved_hash"]:
                raise CoreError("step changed after approval")
            before = dx.parse(self._exec(d, tool.verify_module, {}, actor)[0])
            self._exec(d, tool.id, st["params"], actor)
            after = dx.parse(self._exec(d, tool.verify_module, {}, actor)[0])
            ok, detail = dx.CHECKS[tool.verify_check](st["params"], before, after)
        except (CoreError, ToolError, ValueError, KeyError, TransportError) as e:
            st["status"] = "blocked" if "session" in str(e) or "authorized" in str(e) else "failed"
            st["result"] = {"verified": False, "detail": str(e)}
            self.db.audit(actor, "plan.step_failed", d["id"], plan=p["id"], step=st["id"], error=str(e)[:200])
            return
        st["status"] = "verified" if ok else "unverified"
        st["result"] = {"verified": ok, "detail": detail}
        self.db.audit(actor, "plan.step_result", d["id"], plan=p["id"], step=st["id"], verified=ok, detail=detail)

    # ---------------------------------------------------------------- notes, audit
    def add_note(self, did: str, author: str, body: str) -> dict:
        self.device(did)
        nid = new_id("note")
        self.db.execute("INSERT INTO notes(id,device_id,author,body,created_at) VALUES(?,?,?,?,?)", (nid, did, author, body, self.now()))
        self.db.audit(author, "note.added", did, note=nid)
        return {"id": nid, "device_id": did, "author": author, "body": body}

    def notes(self, did: str) -> list[dict]:
        return self.db.all("SELECT * FROM notes WHERE device_id=? ORDER BY created_at DESC", (did,))

    def audit_log(self, did: str | None = None, limit: int = 500) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit" + (" WHERE device_id=?" if did else "") + " ORDER BY seq DESC LIMIT ?",
                           ((did, limit) if did else (limit,)))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows
