import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.netpolicy import NetworkPolicyError, assert_private_address
from app.tools import REGISTRY, Risk, ToolError, get, validate_console
from app.transports.ssh import build_argv
from tests.conftest import onboard


def test_api_requires_token(core):
    c = TestClient(create_app(core.s, core))
    assert c.get("/devices").status_code == 401
    assert c.get("/devices", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/health").status_code == 200


@pytest.mark.parametrize("h", ["192.168.1.5", "10.0.0.7", "172.16.4.2", "169.254.10.10", "100.101.102.103", "127.0.0.1"])
def test_private_addresses_allowed(h):
    assert_private_address(h)


@pytest.mark.parametrize("h", ["8.8.8.8", "1.1.1.1", "203.0.113.9", "100.128.0.1"])
def test_public_addresses_rejected(h):
    with pytest.raises(NetworkPolicyError):
        assert_private_address(h)


def test_public_device_rejected_and_audited(client):
    r = client.post("/devices", json={"name": "x", "address": "8.8.8.8", "transport": "ssh", "owner_name": "o",
                                      "owner_contact": "o@x.io", "authorization_note": "signed authorization"})
    assert r.status_code == 422
    assert any(a["action"] == "device.rejected" for a in client.get("/audit").json())


@pytest.mark.parametrize("cmd", ["Get-Process | Out-File x", "Get-Process; Remove-Item C:\\", "Get-Process $(calc)", "Remove-Item C:\\x",
                                 "Invoke-Expression 'x'", "Get-Process `n", "Get-Content C:\\secret", "Get-Process > out.txt",
                                 "Get-Service & calc", "powershell -c x", "Get-Process 'a'", "Get-Process\nRemove-Item x", ""])
def test_console_rejects_unsafe(cmd):
    with pytest.raises(ToolError):
        validate_console(cmd)


def test_console_accepts_readonly():
    assert validate_console("Get-Service -Name Spooler") == "Get-Service -Name Spooler"
    assert validate_console("Get-Process") == "Get-Process"


def test_unknown_tool_not_in_allowlist():
    with pytest.raises(ToolError):
        get("Invoke-Expression")


def test_param_validation_blocks_injection():
    t = get("uninstall_software")
    for bad in ["x; Remove-Item C:\\", "$(calc)", "a'b", "", "x" * 200, 5, None]:
        with pytest.raises(ToolError):
            t.validate({"name": bad})
    with pytest.raises(ToolError):
        t.validate({"name": "ok", "extra": 1})
    assert t.validate({"name": "Shady Toolbar 1.0"}) == {"name": "Shady Toolbar 1.0"}


def test_params_are_never_interpolated_into_script():
    t = get("disable_startup_item")
    txt = t.render({"name": "App1"})
    assert "App1" not in txt  # only base64 travels
    assert "FromBase64String" in txt


def test_risk_classification():
    assert all(t.risk == Risk.READ_ONLY for t in REGISTRY.values() if t.kind == "diagnostic")
    high = {t.id for t in REGISTRY.values() if t.risk == Risk.HIGH}
    assert {"clear_temp_files", "reset_winsock", "uninstall_software", "enable_defender_realtime"} <= high
    assert all(t.verify_module and t.verify_check for t in REGISTRY.values() if t.kind == "repair")


def test_ssh_argv_is_strict():
    a = build_argv("192.168.1.9", "tech", "/k", "/kh", "Get-Date")
    assert isinstance(a, list) and "StrictHostKeyChecking=yes" in a and "BatchMode=yes" in a and "PasswordAuthentication=no" in a
    assert a[-2] == "-EncodedCommand"


def test_dpapi_unavailable_off_windows_refuses_to_store(client):
    d, _ = onboard(client)
    r = client.app.state.core
    with pytest.raises(RuntimeError):
        r.set_credentials(d, {"user": "u", "password": "p"})
