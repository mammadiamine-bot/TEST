"""WinRM over HTTPS (5986) with certificate validation. UNTESTED against a real Windows host.
Requires `pywinrm`. Credentials come from the DPAPI-protected store, never from source."""
from __future__ import annotations

import time

from ..netpolicy import assert_private_address
from ..tools import Tool, console_script
from .base import Result, TransportError


class WinRmTransport:
    def __init__(self, host: str, username: str, password: str, ca_trust: str | None, fingerprint: str):
        assert_private_address(host)
        try:
            import winrm  # type: ignore
        except ImportError as e:
            raise TransportError("pywinrm is not installed") from e
        self.fp = fingerprint
        self.session = winrm.Session(f"https://{host}:5986/wsman", auth=(username, password), transport="ntlm",
                                     server_cert_validation="validate", ca_trust_path=ca_trust)

    def host_fingerprint(self) -> str:
        return self.fp

    def _run(self, script: str, timeout_s: int) -> Result:
        t0 = time.time()
        self.session.protocol.operation_timeout_sec = timeout_s
        self.session.protocol.read_timeout_sec = timeout_s + 10
        r = self.session.run_ps(script)
        return Result(r.status_code == 0, r.std_out.decode(errors="replace"), r.std_err.decode(errors="replace")[-2000:], int((time.time() - t0) * 1000))

    def run(self, tool: Tool, params: dict, timeout_s: int) -> Result:
        return self._run(tool.render(params), timeout_s)

    def run_console(self, command: str, timeout_s: int) -> Result:
        return self._run(console_script(command), timeout_s)
