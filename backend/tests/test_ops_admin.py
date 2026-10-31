import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import backup
from app.api import create_app
from app.config import Settings
from app.db import SCHEMA_VERSION, Db
from app.main import JsonFormatter
from app.service import Core
from tests.test_tasks import XorProtector


def make(tmp_path):
    s = Settings(data_dir=tmp_path / "c", api_token="owner-token")
    core = Core(s, Db(s.data_dir / "cit.sqlite3"), protector=XorProtector(), cloud_probe=lambda: True)
    return s, core, create_app(s, core)


def test_backup_is_consistent_verified_and_restorable(tmp_path):
    s, core, app = make(tmp_path)
    owner = TestClient(app, headers={"Authorization": "Bearer owner-token"})
    oid = owner.post("/orgs", json={"name": "Alpha"}).json()["id"]
    f = backup.create(s.data_dir / "cit.sqlite3", tmp_path / "bk")
    assert backup.verify(f)["audit_chain"] == "ok" and f.with_suffix(".sha256").exists() and f.with_suffix(".license.key").exists()
    owner.post("/orgs", json={"name": "Beta"})                                   # changes after the backup
    core.db.conn.close()
    backup.restore(f, s.data_dir / "cit.sqlite3")
    names = [r[0] for r in sqlite3.connect(s.data_dir / "cit.sqlite3").execute("SELECT name FROM orgs")]
    assert names == ["Alpha"] and list(s.data_dir.glob("cit.before-restore-*"))  # restored, previous state kept


def test_tampered_backup_is_refused(tmp_path):
    s, core, app = make(tmp_path)
    TestClient(app, headers={"Authorization": "Bearer owner-token"}).post("/orgs", json={"name": "Alpha"})
    f = backup.create(s.data_dir / "cit.sqlite3", tmp_path / "bk")
    c = sqlite3.connect(f); c.execute("UPDATE audit SET detail='{}' WHERE seq=1"); c.commit(); c.close()
    with pytest.raises(SystemExit, match="audit chain broken"):
        backup.verify(f)
    with pytest.raises(SystemExit):
        backup.restore(f, tmp_path / "x.sqlite3")


def test_ready_endpoint_and_health_expose_no_tenant_data(tmp_path):
    _, _, app = make(tmp_path)
    c = TestClient(app)
    assert c.get("/health").json() == {"ok": True} and c.get("/ready").json() == {"ready": True}


def test_old_database_gets_a_safety_copy_before_migration(tmp_path):
    p = tmp_path / "old.sqlite3"
    c = sqlite3.connect(p); c.execute("CREATE TABLE devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, transport TEXT NOT NULL, owner_name TEXT NOT NULL, owner_contact TEXT NOT NULL, authorization_note TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)")
    c.execute("INSERT INTO devices VALUES('d','n','a','mock','o','c','n','paired',1.0)"); c.commit(); c.close()
    Db(p)
    assert (tmp_path / f"old.sqlite3.pre-v{SCHEMA_VERSION}.bak").exists()
    assert sqlite3.connect(p).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    Db(p)  # up to date: no second copy needed, and it opens cleanly
    assert len(list(tmp_path.glob("old.sqlite3.pre-*"))) == 1


def test_structured_log_line_is_json_without_secrets():
    r = logging.LogRecord("x", logging.WARNING, __file__, 1, "worker %s offline", ("wst_1",), None)
    d = json.loads(JsonFormatter().format(r))
    assert d["level"] == "WARNING" and d["msg"] == "worker wst_1 offline" and d["ts"].endswith("Z")


def test_rmm_tables_from_an_earlier_build_gain_later_columns(tmp_path):
    p = tmp_path / "mid.sqlite3"
    c = sqlite3.connect(p)   # a controller database created before schedules.approved_by / job_runs.remote existed
    c.executescript("CREATE TABLE schedules(id TEXT PRIMARY KEY, org_id TEXT NOT NULL, title TEXT NOT NULL, shell TEXT NOT NULL, body TEXT NOT NULL, risk TEXT NOT NULL, targets TEXT NOT NULL, every_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, next_run REAL NOT NULL, last_run REAL, created_by TEXT NOT NULL, created_at REAL NOT NULL);"
                    "CREATE TABLE job_runs(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, worker_id TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, exit_code INTEGER, output TEXT DEFAULT '', started_at REAL, finished_at REAL, error TEXT);")
    c.commit(); c.close()
    db = Db(p)
    assert db.one("SELECT approved_by FROM schedules") is None and db.all("SELECT remote FROM job_runs") == []
