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
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  device_id TEXT, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Db:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

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
