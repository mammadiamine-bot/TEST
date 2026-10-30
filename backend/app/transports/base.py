from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..tools import Tool


class TransportError(RuntimeError):
    pass


@dataclass
class Result:
    ok: bool
    output: str
    error: str = ""
    duration_ms: int = 0


class Transport(Protocol):
    def run(self, tool: Tool, params: dict, timeout_s: int) -> Result: ...
    def run_console(self, command: str, timeout_s: int) -> Result: ...
    def host_fingerprint(self) -> str: ...
