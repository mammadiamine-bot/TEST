"""Consistent online backup / verify / restore of the controller database (and the license key beside it).

    python -m app.backup create  [--data-dir D] [--out DIR]
    python -m app.backup verify  FILE
    python -m app.backup restore FILE [--data-dir D]      (refuses while a controller holds the DB unless --force)
Backups use SQLite's online backup API, so they are consistent while the controller runs. Secrets that are DPAPI-protected stay
protected (they only open for the same Windows user); keep the data directory's other files (license.key) with the backup.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def default_dir() -> Path:
    return Path(os.environ.get("CIT_DATA_DIR", Path.home() / ".claude-it-technician"))


def create(db_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"cit-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    verify(dest)
    key = db_path.parent / "license.key"
    if key.exists():
        shutil.copy2(key, dest.with_suffix(".license.key"))
    (dest.with_suffix(".sha256")).write_text(hashlib.sha256(dest.read_bytes()).hexdigest() + "  " + dest.name + "\n")
    return dest


def verify(f: Path) -> dict:
    c = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    try:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise SystemExit(f"integrity check failed: {ok}")
        n = c.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        prev, bad = "0" * 64, 0
        import json  # noqa: F401
        for r in c.execute("SELECT ts,actor,action,device_id,detail,prev_hash,hash FROM audit ORDER BY seq"):
            h = hashlib.sha256(f"{prev}|{r[0]!r}|{r[1]}|{r[2]}|{r[3]}|{r[4]}".encode()).hexdigest()
            bad += (h != r[6] or r[5] != prev); prev = r[6]
        if bad:
            raise SystemExit(f"audit chain broken in {bad} record(s)")
        return {"integrity": "ok", "audit_records": n, "audit_chain": "ok"}
    finally:
        c.close()


def restore(f: Path, db_path: Path, force: bool = False) -> None:
    verify(f)
    sha = f.with_suffix(".sha256")
    if sha.exists() and sha.read_text().split()[0] != hashlib.sha256(f.read_bytes()).hexdigest():
        raise SystemExit("backup file does not match its recorded SHA-256")
    if db_path.exists():
        try:
            c = sqlite3.connect(db_path, timeout=1); c.execute("BEGIN EXCLUSIVE"); c.rollback(); c.close()
        except sqlite3.OperationalError:
            if not force:
                raise SystemExit("the database is in use - stop the controller first (or --force)")
        keep = db_path.with_suffix(f".before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(db_path, keep)
        print("previous database kept as", keep)
    shutil.copy2(f, db_path)
    key = f.with_suffix(".license.key")
    if key.exists() and not (db_path.parent / "license.key").exists():
        shutil.copy2(key, db_path.parent / "license.key")
    print("restored", db_path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="app.backup")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--data-dir", type=Path, default=default_dir()); c.add_argument("--out", type=Path)
    v = sub.add_parser("verify"); v.add_argument("file", type=Path)
    r = sub.add_parser("restore"); r.add_argument("file", type=Path); r.add_argument("--data-dir", type=Path, default=default_dir()); r.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "create":
        print(create(a.data_dir / "cit.sqlite3", a.out or a.data_dir / "backups"))
    elif a.cmd == "verify":
        print(verify(a.file))
    else:
        restore(a.file, a.data_dir / "cit.sqlite3", a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
