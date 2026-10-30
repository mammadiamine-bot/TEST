"""Simulated Windows machine. Same JSON shapes as the PowerShell scripts, with mutable state so
repairs really change what the next diagnostic sees. Safe for tests; touches nothing real."""
from __future__ import annotations

import copy
import json
import math
import time

from ..tools import Tool, validate_console
from .base import Result

BROKEN = {
    "cpu_ram": {"cpu_percent": 34, "ram_total_gb": 16.0, "ram_used_gb": 15.1, "ram_percent": 94},
    "performance": {"uptime_hours": 900, "pending_reboot": True, "top_processes": [{"name": "chrome", "ws_mb": 2100}, {"name": "Teams", "ws_mb": 900}]},
    "startup": {"items": [{"name": f"App{i}", "command": f"C:\\Apps\\app{i}.exe", "location": "HKU\\Run"} for i in range(1, 16)]},
    "disk_health": {"disks": [{"model": "Samsung SSD 860", "health": "Healthy", "operational": "OK", "media": "SSD"}], "smart_predict_failure": False},
    "windows_update": {"services": {"wuauserv": "Stopped", "bits": "Running"}, "last_update_days": 95,
                       "failures": [{"message": "0x80070422: The service cannot be started", "date": "2026-10-25"}]},
    "drivers": {"problem_devices": [{"name": "Unknown device", "class": "Other", "error_code": 28}]},
    "network": {"adapters": [{"name": "Ethernet", "ip": "192.168.1.20", "gateway": "192.168.1.1"}], "gateway_reachable": True, "internet_reachable": True, "dns_resolves": False},
    "dns_dhcp": {"adapters": [{"name": "Ethernet", "dhcp": True, "ip": "192.168.1.20", "apipa": False, "dns": ["192.168.1.1"]}], "dns_resolves": False},
    "printers": {"spooler": "Stopped", "printers": [{"name": "Office HP", "status": "Error", "jobs": 4}]},
    "event_log": {"critical": 1, "errors": 120, "top_sources": [{"source": "Disk", "count": 20, "sample": "The device has a bad block"}, {"source": "DCOM", "count": 80, "sample": "DCOM got error"}]},
    "integrity": {"dism_checkhealth": "repairable", "defender_realtime": False, "signature_age_days": 12},
    "software": {"apps": [{"name": "Google Chrome", "version": "130", "publisher": "Google"}, {"name": "Shady Toolbar", "version": "1.0", "publisher": "Unknown"}]},
    "storage": {"volumes": [{"drive": "C", "size_gb": 237.0, "free_gb": 9.0}], "temp_mb": 6400},
}

HEALTHY = copy.deepcopy(BROKEN)
HEALTHY["cpu_ram"].update(ram_used_gb=6.0, ram_percent=37)
HEALTHY["performance"].update(uptime_hours=20, pending_reboot=False)
HEALTHY["startup"]["items"] = HEALTHY["startup"]["items"][:5]
HEALTHY["windows_update"] = {"services": {"wuauserv": "Running", "bits": "Running"}, "last_update_days": 7, "failures": []}
HEALTHY["drivers"]["problem_devices"] = []
HEALTHY["network"].update(dns_resolves=True)
HEALTHY["dns_dhcp"]["dns_resolves"] = True
HEALTHY["printers"] = {"spooler": "Running", "printers": [{"name": "Office HP", "status": "Normal", "jobs": 0}]}
HEALTHY["event_log"] = {"critical": 0, "errors": 3, "top_sources": []}
HEALTHY["integrity"] = {"dism_checkhealth": "healthy", "defender_realtime": True, "signature_age_days": 1}
HEALTHY["storage"] = {"volumes": [{"drive": "C", "size_gb": 237.0, "free_gb": 120.0}], "temp_mb": 100}

SCENARIOS = {"broken": BROKEN, "healthy": HEALTHY}
CONSOLE_OUT = {"Get-Process": "Handles  ProcessName\n  1200  chrome\n   800  Teams", "Get-Service": "Status   Name\nRunning  Dhcp\nStopped  Spooler"}


class MockTransport:
    def __init__(self, scenario: str = "broken", ineffective: set[str] | None = None, fail_tools: set[str] | None = None, latency: float = 0.0):
        self.state = copy.deepcopy(SCENARIOS[scenario])
        self.ineffective = ineffective or set()  # tool runs "ok" but changes nothing -> verification must catch it
        self.fail_tools = fail_tools or set()
        self.latency = latency
        self.executed: list[tuple[str, dict]] = []

    def host_fingerprint(self) -> str:
        return "mock-fingerprint-0001"

    def run(self, tool: Tool, params: dict, timeout_s: int) -> Result:
        t0 = time.time()
        if self.latency:
            time.sleep(self.latency)
        if tool.kind == "diagnostic":
            data = self.state[tool.id]
            if tool.id == "cpu_ram":  # simulated load wave so the live chart moves (mock only)
                data = {**data, "cpu_percent": int(max(1, min(99, data["cpu_percent"] + 20 * math.sin(time.time() / 4))))}
            return Result(True, json.dumps(data), duration_ms=int((time.time() - t0) * 1000))
        self.executed.append((tool.id, dict(params)))
        if tool.id in self.fail_tools:
            return Result(False, "", f"{tool.id} failed on device", 5)
        if tool.id not in self.ineffective:
            self._apply(tool.id, params)
        return Result(True, '"ok"', duration_ms=int((time.time() - t0) * 1000))

    def _apply(self, tid: str, p: dict) -> None:
        s = self.state
        if tid == "flush_dns":
            s["dns_dhcp"]["dns_resolves"] = True; s["network"]["dns_resolves"] = True
        elif tid == "renew_dhcp":
            for a in s["dns_dhcp"]["adapters"]: a["apipa"] = False
        elif tid == "restart_spooler":
            s["printers"]["spooler"] = "Running"
        elif tid == "clear_print_queue":
            for pr in s["printers"]["printers"]: pr["jobs"] = 0; pr["status"] = "Normal"
            s["printers"]["spooler"] = "Running"
        elif tid == "restart_update_services":
            s["windows_update"]["services"] = {"wuauserv": "Running", "bits": "Running"}
        elif tid == "disable_startup_item":
            s["startup"]["items"] = [i for i in s["startup"]["items"] if i["name"] != p["name"]]
        elif tid == "run_dism_restorehealth":
            s["integrity"]["dism_checkhealth"] = "healthy"
        elif tid == "clear_temp_files":
            freed = s["storage"]["temp_mb"] / 1024
            s["storage"]["temp_mb"] = 50
            for v in s["storage"]["volumes"]: v["free_gb"] = round(v["free_gb"] + freed, 1)
        elif tid == "reset_winsock":
            s["network"]["internet_reachable"] = True
        elif tid == "uninstall_software":
            s["software"]["apps"] = [a for a in s["software"]["apps"] if a["name"] != p["name"]]
        elif tid == "enable_defender_realtime":
            s["integrity"]["defender_realtime"] = True

    def run_console(self, command: str, timeout_s: int) -> Result:
        cmd = validate_console(command)
        return Result(True, CONSOLE_OUT.get(cmd.split()[0], f"(mock) {cmd}: no data"))
