from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, transport TEXT NOT NULL,
  owner_name TEXT NOT NULL, owner_contact TEXT NOT NULL, authorization_note TEXT NOT NULL,
  status TEXT NOT NULL, fingerprint TEXT, pairing_hash TEXT, pairing_expires REAL,
  scenario TEXT, created_at REAL NOT NULL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
  requested_by TEXT NOT NULL, consent_hash TEXT, consenting_person TEXT,
  duration_minutes INTEGER NOT NULL, created_at REAL NOT NULL, activated_at REAL, expires_at REAL, ended_at REAL);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, created_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL,
  created_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS offline_queue(
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, payload TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS credentials(
  device_id TEXT PRIMARY KEY, blob BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, name TEXT NOT NULL, root TEXT NOT NULL, stack TEXT NOT NULL,
  build_cmd TEXT, test_cmd TEXT, deploy_cmd TEXT, git_url TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, project_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
  created_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workers(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, fingerprint TEXT NOT NULL, worker_id TEXT, credential_id TEXT,
  status TEXT NOT NULL, rustdesk_id TEXT, notes TEXT, created_at REAL NOT NULL, last_seen REAL);
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  device_id TEXT, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orgs(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sites(
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, notes TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS principals(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, org_ids TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, last_used REAL);
"""

# additive, idempotent migrations for databases created before multi-client support (existing rows keep org_id NULL = unassigned)
RMM_SCHEMA = """
CREATE TABLE IF NOT EXISTS principal_controllers(principal_id TEXT NOT NULL, controller_id TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS pc_principal ON principal_controllers(principal_id);
CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tags(kind TEXT NOT NULL, rid TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(kind, rid, tag));
CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(org_id, name));
CREATE TABLE IF NOT EXISTS group_members(group_id TEXT NOT NULL, kind TEXT NOT NULL, rid TEXT NOT NULL, PRIMARY KEY(group_id, kind, rid));
CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id TEXT NOT NULL, ts REAL NOT NULL, online INTEGER NOT NULL, cpu REAL, ram REAL, disk REAL);
CREATE INDEX IF NOT EXISTS samples_w_ts ON samples(worker_id, ts);
CREATE TABLE IF NOT EXISTS alert_rules(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, metric TEXT NOT NULL, op TEXT NOT NULL, threshold REAL NOT NULL, consecutive INTEGER NOT NULL DEFAULT 3, severity TEXT NOT NULL DEFAULT 'warning', enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, worker_id TEXT NOT NULL, rule_id TEXT, kind TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', first_seen REAL NOT NULL, last_seen REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1, acked_by TEXT, acked_at REAL, resolved_at REAL, dedup TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS alerts_dedup ON alerts(dedup, state);
CREATE TABLE IF NOT EXISTS notify_policies(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, channel TEXT NOT NULL, target TEXT NOT NULL, min_severity TEXT NOT NULL DEFAULT 'warning', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, alert_id TEXT, channel TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scripts(id TEXT PRIMARY KEY, org_id TEXT, name TEXT NOT NULL, shell TEXT NOT NULL, risk TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS script_versions(script_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, author TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(script_id, version));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, title TEXT NOT NULL, shell TEXT NOT NULL, body TEXT NOT NULL, risk TEXT NOT NULL, script_id TEXT, script_version INTEGER, status TEXT NOT NULL, approval TEXT NOT NULL DEFAULT 'not_required', approved_by TEXT, created_by TEXT NOT NULL, created_at REAL NOT NULL, retries INTEGER NOT NULL DEFAULT 0, timeout INTEGER NOT NULL DEFAULT 300, schedule_id TEXT);
CREATE TABLE IF NOT EXISTS job_runs(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, worker_id TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, remote TEXT, exit_code INTEGER, output TEXT DEFAULT '', started_at REAL, finished_at REAL, error TEXT);
CREATE INDEX IF NOT EXISTS job_runs_w ON job_runs(worker_id, started_at);
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, title TEXT NOT NULL, shell TEXT NOT NULL, body TEXT NOT NULL, risk TEXT NOT NULL, targets TEXT NOT NULL, every_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, approved_by TEXT, next_run REAL NOT NULL, last_run REAL, created_by TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions(org_id TEXT PRIMARY KEY, plan TEXT NOT NULL, status TEXT NOT NULL, trial_ends REAL, period_start REAL NOT NULL, period_end REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS subscription_history(id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, ts REAL NOT NULL, actor TEXT NOT NULL, change TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(org_id TEXT NOT NULL, day TEXT NOT NULL, devices INTEGER NOT NULL, jobs INTEGER NOT NULL DEFAULT 0, ai_tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(org_id, day));
CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, worker_id TEXT, alert_id TEXT, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open', created_by TEXT NOT NULL, created_at REAL NOT NULL, due_at REAL, resolved_at REAL);
CREATE TABLE IF NOT EXISTS ai_runs(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, worker_id TEXT, actor TEXT NOT NULL, request TEXT NOT NULL, engine TEXT NOT NULL, model TEXT, plan TEXT, answer TEXT, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, status TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS inventory(worker_id TEXT NOT NULL, kind TEXT NOT NULL, ts REAL NOT NULL, data TEXT NOT NULL, PRIMARY KEY(worker_id, kind));
CREATE TABLE IF NOT EXISTS remote_sessions(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, worker_id TEXT NOT NULL, operator TEXT NOT NULL, engine TEXT NOT NULL, started_at REAL NOT NULL, expires_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL, controllable INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS android_devices(host TEXT NOT NULL, serial TEXT NOT NULL, org_id TEXT NOT NULL, name TEXT, PRIMARY KEY(host, serial));
CREATE TABLE IF NOT EXISTS android_actions(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, host TEXT NOT NULL, serial TEXT NOT NULL, kind TEXT NOT NULL, arg TEXT, status TEXT NOT NULL, created_by TEXT NOT NULL, approved_by TEXT, created_at REAL NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS chat_conversations(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, user_id TEXT NOT NULL, title TEXT NOT NULL, host TEXT NOT NULL, model TEXT NOT NULL, worker_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, ms INTEGER DEFAULT 0, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS chat_msg_conv ON chat_messages(conv_id, id);
CREATE TABLE IF NOT EXISTS agent_sessions(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, actor TEXT NOT NULL, request TEXT NOT NULL, lang TEXT, target_kind TEXT NOT NULL, target TEXT NOT NULL, host TEXT, model TEXT, intent TEXT, status TEXT NOT NULL, outcome TEXT, resolution TEXT, level INTEGER DEFAULT 1, answer TEXT DEFAULT '', report TEXT, cancel INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL);
CREATE INDEX IF NOT EXISTS agent_sessions_org ON agent_sessions(org_id, created_at);
CREATE TABLE IF NOT EXISTS agent_events(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, title TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS agent_events_s ON agent_events(session_id, id);
CREATE TABLE IF NOT EXISTS agent_actions(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, org_id TEXT NOT NULL, target_kind TEXT NOT NULL, target TEXT NOT NULL, workflow TEXT NOT NULL, step_idx INTEGER DEFAULT 0, template TEXT, params TEXT, title TEXT, risk TEXT, cls TEXT, status TEXT NOT NULL, job_id TEXT, created_by TEXT, approved_by TEXT, auto INTEGER DEFAULT 0, output TEXT, error TEXT, verify TEXT, created_at REAL NOT NULL, finished_at REAL);
CREATE INDEX IF NOT EXISTS agent_actions_s ON agent_actions(session_id);
CREATE TABLE IF NOT EXISTS agent_policy(org_id TEXT PRIMARY KEY, level INTEGER NOT NULL DEFAULT 1, max_auto_per_hour INTEGER NOT NULL DEFAULT 6, updated_by TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS agent_maintenance(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, title TEXT, targets TEXT NOT NULL, workflows TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL, created_by TEXT, approved_by TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS agent_state(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS kb_procedures(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, key TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL, signature TEXT NOT NULL, workflow TEXT, body TEXT, status TEXT NOT NULL, source TEXT NOT NULL, created_by TEXT, approved_by TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kb_cases(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, os TEXT, host TEXT, request TEXT, signature TEXT, tokens TEXT, root_cause TEXT, causes TEXT, evidence TEXT, workflow TEXT, outcome TEXT, verified INTEGER DEFAULT 0, confidence REAL DEFAULT 0, feedback INTEGER DEFAULT 0, status TEXT NOT NULL, session_id TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS kb_cases_org ON kb_cases(org_id, created_at);
CREATE TABLE IF NOT EXISTS model_bench(host TEXT NOT NULL, model TEXT NOT NULL, ts REAL NOT NULL, json_ok INTEGER, arabic_ok INTEGER, ms INTEGER, tps REAL, PRIMARY KEY(host, model));
CREATE TABLE IF NOT EXISTS org_settings(org_id TEXT PRIMARY KEY, ai_enabled INTEGER NOT NULL DEFAULT 1, ai_cloud INTEGER NOT NULL DEFAULT 0, retention_days INTEGER NOT NULL DEFAULT 30);
"""

SCHEMA_VERSION = 10  # 1 = M1-M5, 2 = tenancy, 3 = RMM tables, 4 = columns added to RMM tables
MIGRATIONS = [("principals", "created_by", "TEXT"), ("principals", "rotated_by", "TEXT"), ("schedules", "approved_by", "TEXT"), ("job_runs", "remote", "TEXT"), ("orgs", "status", "TEXT DEFAULT 'active'"), ("orgs", "retention_until", "REAL"), ("orgs", "offboarded_at", "REAL"), ("devices", "org_id", "TEXT"), ("devices", "site_id", "TEXT"), ("workers", "org_id", "TEXT"), ("workers", "site_id", "TEXT")]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Db:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        if str(path) != ":memory:" and Path(path).exists() and Path(path).stat().st_size > 0:
            probe = sqlite3.connect(str(path))
            ver = probe.execute("PRAGMA user_version").fetchone()[0]; probe.close()
            if ver < SCHEMA_VERSION:  # safe migrations: keep a byte-for-byte copy before the schema is touched
                import shutil
                shutil.copy2(path, str(path) + f".pre-v{SCHEMA_VERSION}.bak")
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.executescript(RMM_SCHEMA)
            for table, col, typ in MIGRATIONS:
                if col not in {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            for col, reason in (("created_by", "created"), ("rotated_by", "rotated")):  # backfill the append-only control history from the older single columns
                self.conn.execute(f"INSERT INTO principal_controllers(principal_id,controller_id,reason,at) SELECT p.id, p.{col}, '{reason}', COALESCE(p.created_at,0) FROM principals p "
                                  f"WHERE p.{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM principal_controllers c WHERE c.principal_id=p.id AND c.controller_id=p.{col} AND c.reason='{reason}')")
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---- tamper-evident audit log (hash chain) ----
    def audit(self, actor: str, action: str, device_id: str | None = None, **detail: Any) -> None:
        d = json.dumps(detail, sort_keys=True, default=str)
        with self.lock:
            row = self.conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            ts = time.time()
            h = hashlib.sha256(f"{prev}|{ts!r}|{actor}|{action}|{device_id}|{d}".encode()).hexdigest()
            self.conn.execute(
                "INSERT INTO audit(ts,actor,action,device_id,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
                (ts, actor, action, device_id, d, prev, h))
            self.conn.commit()

    def verify_audit_chain(self) -> bool:
        prev = "0" * 64
        for r in self.all("SELECT * FROM audit ORDER BY seq"):
            h = hashlib.sha256(
                f"{prev}|{r['ts']!r}|{r['actor']}|{r['action']}|{r['device_id']}|{r['detail']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False
            prev = h
        return True
