import json
import socket
import subprocess
import threading

import pytest

from app import connections as C
from app.netpolicy import NetworkPolicyError
from app.features import FEATURES, matrix


def listener():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5)
    threading.Thread(target=lambda: [s.accept() for _ in range(3)], daemon=True).start()
    return s, s.getsockname()[1]


def test_probe_rejects_public_and_finds_nothing_on_closed_host():
    with pytest.raises(NetworkPolicyError):
        C.probe("8.8.8.8")
    r = C.probe("127.0.0.1")
    assert r["host"] == "127.0.0.1" and isinstance(r["open"], list)
    assert any("USB cable" in a for a in r["advice"]) or r["open"]


def test_probe_detects_listening_agent_port(monkeypatch):
    s, port = listener()
    monkeypatch.setattr(C, "PORTS", [(port, "agent", "Technician agent")])
    r = C.probe("127.0.0.1")
    assert r["open"][0]["method"] == "agent" and "agent detected" in r["advice"][0]


def test_methods_cover_requested_list():
    ids = {m["id"] for m in C.METHODS}
    assert {"ethernet", "lan", "usb_agent", "rustdesk", "ssh", "winrm", "tailscale", "vps", "git", "cloud_api"} <= ids
    assert any("does NOT create a network" in " ".join(m["steps"]) for m in C.METHODS if m["id"] == "ethernet")


def test_git_check_real_local_repo(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x", "PATH": "/usr/bin:/bin"}
    for c in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *c], check=True, env=env)
    r = C.git_check(f"file://{repo}")
    assert r["ok"] and r["branches"] == ["main"]
    bad = C.git_check(f"file://{tmp_path}/missing")
    assert not bad["ok"] and bad["hint"]
    assert not C.git_check("--upload-pack=evil")["ok"] and not C.git_check("http://insecure/x")["ok"]


def test_tailscale_parser_and_unavailable():
    sample = {"BackendState": "Running", "Peer": {"k1": {"HostName": "client-pc", "TailscaleIPs": ["100.64.0.7"], "Online": True, "OS": "windows"}}}
    r = C.tailscale_peers(lambda: (0, json.dumps(sample)))
    assert r["available"] and r["peers"][0]["ip"] == "100.64.0.7"
    assert not C.tailscale_peers(lambda: (1, ""))["available"]
    def boom(): raise OSError()
    assert not C.tailscale_peers(boom)["available"]


def test_rustdesk_launch_never_passes_password(monkeypatch):
    calls = []
    monkeypatch.setattr(C.shutil, "which", lambda n: "/usr/bin/rustdesk" if n == "rustdesk" else None)
    r = C.rustdesk_launch("123456789", popen=lambda argv, shell: calls.append(argv))
    assert r["launched"] and calls == [["/usr/bin/rustdesk", "--connect", "123456789"]]
    for bad in ("1; calc", "--password x", "", "a b"):
        with pytest.raises(ValueError):
            C.rustdesk_launch(bad)
    monkeypatch.setattr(C.shutil, "which", lambda n: None)
    assert not C.rustdesk_launch("123456789")["launched"]


def test_feature_matrix_is_well_formed_and_honest():
    allowed = {"tested", "untested", "mock", "planned", "blocked"}
    assert all(f[3] in allowed for f in FEATURES) and len({f[0] for f in FEATURES}) == len(FEATURES)
    m = {f["id"]: f for f in matrix()}
    assert m["conn.usb_cable"]["status"] == "blocked" and m["claude.central"]["status"] == "untested" and m["agent.persistent"]["status"] == "planned"
