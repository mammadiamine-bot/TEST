import os
import stat
import sys
from pathlib import Path

import pytest

from app.servers import SERVER_TOOLS, LinuxSshTarget, run_sync, server_tool, ssh_base
from app.tools import Risk, ToolError

FAKE = str(Path(__file__).parent / "fake_ssh.py")


@pytest.fixture
def ssh(tmp_path):
    wrapper = tmp_path / "ssh"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n"); wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return LinuxSshTarget("10.0.0.5", "ops", "/k", tmp_path / "kh", 22, str(wrapper))


def test_base_is_strict():
    b = ssh_base("10.0.0.5", "ops", "/k", "/kh", 2222)
    assert "StrictHostKeyChecking=yes" in b and "BatchMode=yes" in b and "PasswordAuthentication=no" in b and b[-1] == "--"


@pytest.mark.parametrize("tid,params", [("svc_status", {"unit": "nginx; rm -rf /"}), ("svc_restart", {"unit": "$(id)"}), ("docker_logs", {"name": "-v /:/x", "lines": 5}),
                                       ("net_ping", {"host": "-f"}), ("backup_create", {"path": "/etc/../root", "dest": "/tmp/x.tgz"}),
                                       ("svc_logs", {"unit": "nginx", "lines": 99999}), ("srv_uptime", {"x": 1}), ("svc_status", {})])
def test_params_rejected(tid, params):
    with pytest.raises(ToolError):
        server_tool(tid).build(params)


def test_build_argv_and_sudo():
    assert server_tool("svc_status").build({"unit": "nginx.service"}) == ["systemctl", "status", "nginx.service", "--no-pager"]
    assert server_tool("svc_restart").build({"unit": "nginx"})[:3] == ["sudo", "-n", "--"]
    with pytest.raises(ToolError):
        server_tool("rm_rf")


def test_risk_classes():
    assert all(t.risk == Risk.READ_ONLY for t in SERVER_TOOLS.values() if t.id.startswith(("srv_", "docker_ps", "docker_logs", "svc_status", "svc_logs", "net_", "nginx_test")))
    assert {"svc_restart", "svc_stop", "docker_restart", "backup_restore"} <= {t.id for t in SERVER_TOOLS.values() if t.risk == Risk.HIGH}
    assert all(t.production for t in SERVER_TOOLS.values() if t.risk == Risk.HIGH)


def test_quoting_survives_remote_shell(ssh, tmp_path):
    nasty = "a b; echo INJECTED $(whoami) 'q' \"d\""
    r = run_sync(ssh, [sys.executable, "-c", "import sys;print(sys.argv[1])", nasty], timeout=20)
    assert r["exit"] == 0 and r["output"].strip() == nasty and "INJECTED" in nasty
    out = run_sync(ssh, ["echo", "x;touch " + str(tmp_path / "pwned")])
    assert not (tmp_path / "pwned").exists()


def test_ssh_file_ops_and_cwd(ssh, tmp_path):
    f = tmp_path / "dir with space" / "f.txt"; f.parent.mkdir()
    ssh.fs_write(str(f), b"data\n")
    assert ssh.fs_read(str(f)) == b"data\n"
    assert [e["name"] for e in ssh.fs_list(str(f.parent))] == ["f.txt"]
    assert run_sync(ssh, ["pwd"], cwd=str(f.parent))["output"].strip() == str(f.parent)
    ssh.fs_delete(str(f)); assert not f.exists()


def test_real_readonly_server_tools_run_locally(ssh):
    """These read-only actions are real commands; the fake ssh runs them on this Linux box."""
    for tid in ("srv_uptime", "srv_disk", "srv_memory"):
        r = run_sync(ssh, server_tool(tid).build({}), timeout=20)
        assert r["exit"] == 0 and r["output"].strip(), tid


def test_job_timeout_and_cancel(ssh):
    r = run_sync(ssh, ["sleep", "30"], timeout=1)
    assert r["timed_out"] or r["cancelled"]
