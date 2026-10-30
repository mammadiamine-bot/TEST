"""OpenSSH transport to Windows (OpenSSH Server). UNTESTED against a real Windows host.

Security: argv list (never a shell string), BatchMode, StrictHostKeyChecking=yes against a private
known_hosts file that pairing writes, key-only auth. Script is sent as -EncodedCommand (UTF-16LE base64).
"""
from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path

from ..netpolicy import assert_private_address
from ..tools import Tool, console_script
from .base import Result, TransportError


def build_argv(host: str, user: str, key_path: str, known_hosts: str, script: str, port: int = 22) -> list[str]:
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["ssh", "-p", str(port), "-i", key_path, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=10",
            f"{user}@{host}", "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc]


class SshTransport:
    def __init__(self, host: str, user: str, key_path: str, known_hosts: Path, fingerprint: str, port: int = 22):
        assert_private_address(host)
        self.host, self.user, self.key_path, self.known_hosts, self.fp, self.port = host, user, key_path, str(known_hosts), fingerprint, port

    def host_fingerprint(self) -> str:
        return self.fp

    def _run(self, script: str, timeout_s: int) -> Result:
        t0 = time.time()
        try:
            p = subprocess.run(build_argv(self.host, self.user, self.key_path, self.known_hosts, script, self.port),
                               capture_output=True, text=True, timeout=timeout_s, shell=False)
        except subprocess.TimeoutExpired:
            return Result(False, "", f"timed out after {timeout_s}s", int((time.time() - t0) * 1000))
        except FileNotFoundError as e:
            raise TransportError("ssh client not found") from e
        return Result(p.returncode == 0, p.stdout, p.stderr[-2000:], int((time.time() - t0) * 1000))

    def run(self, tool: Tool, params: dict, timeout_s: int) -> Result:
        return self._run(tool.render(params), timeout_s)

    def run_console(self, command: str, timeout_s: int) -> Result:
        return self._run(console_script(command), timeout_s)
