"""Runs tools/lan_check.py against a REAL backend (uvicorn) and a REAL agent over loopback — proving the script itself works.
This is NOT the LAN test: that needs the second PC."""
import socket, subprocess, sys, threading, time
from pathlib import Path

import pytest
import uvicorn

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core
from app.targetcore import Grants
from tests.test_agent import cit_agent

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def run_script(tmp_path, grants, extra=()):
    proj = tmp_path / "proj"; proj.mkdir()
    s = Settings(data_dir=tmp_path / "d", api_token="tok")
    app = create_app(s, Core(s, Db(":memory:"), cloud_probe=lambda: True))
    bport = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=bport, log_level="error")); threading.Thread(target=srv.run, daemon=True).start()
    ag = cit_agent.Agent([proj], grants, 30, say=lambda *_: None)
    aport = free_port(); asrv, fp = cit_agent.serve(ag, "127.0.0.1", aport)
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", bport), 0.2).close(); break
        except OSError: time.sleep(0.1)
    r = subprocess.run([sys.executable, str(ROOT / "tools/lan_check.py"), "--controller", f"http://127.0.0.1:{bport}", "--token", "tok", "--agent", f"127.0.0.1:{aport}",
                        "--code", ag.code, "--fingerprint", fp, "--root", str(proj), "--non-interactive", *extra], capture_output=True, text=True, timeout=120)
    srv.should_exit = True; asrv.shutdown()
    return r, ag


def test_full_script_passes_with_real_agent(tmp_path):
    r, ag = run_script(tmp_path, Grants(terminal=True, write=True))
    out = r.stdout
    for need in ("[PASS   ] network", "[PASS   ] pair", "[PASS   ] connection test", "[PASS   ] remote command execution (hostname)", "[PASS   ] file transfer", "[PASS   ] simple coding task", "[PASS   ] disconnect"):
        assert need in out, out
    assert r.returncode == 0 and "0 failed" in out and ag.ended.is_set()


def test_script_reports_blocked_not_success_when_owner_denied_permissions(tmp_path):
    r, _ = run_script(tmp_path, Grants())
    assert "[BLOCKED] remote command execution" in r.stdout and "[BLOCKED] file transfer" in r.stdout and "[PASS   ] remote command execution" not in r.stdout


def test_script_fails_honestly_on_wrong_fingerprint(tmp_path):
    proj = tmp_path / "p"; proj.mkdir()
    s = Settings(data_dir=tmp_path / "d", api_token="tok"); app = create_app(s, Core(s, Db(":memory:"), cloud_probe=lambda: True))
    bport = free_port(); srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=bport, log_level="error")); threading.Thread(target=srv.run, daemon=True).start()
    ag = cit_agent.Agent([proj], Grants(), 30, say=lambda *_: None); aport = free_port(); asrv, fp = cit_agent.serve(ag, "127.0.0.1", aport); time.sleep(0.8)
    r = subprocess.run([sys.executable, str(ROOT / "tools/lan_check.py"), "--controller", f"http://127.0.0.1:{bport}", "--token", "tok", "--agent", f"127.0.0.1:{aport}", "--code", ag.code,
                        "--fingerprint", "ab" * 32, "--root", str(proj), "--non-interactive"], capture_output=True, text=True, timeout=60)
    srv.should_exit = True; asrv.shutdown()
    assert r.returncode == 1 and "[FAIL   ] pair" in r.stdout and "identity mismatch" in r.stdout
