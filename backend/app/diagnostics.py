"""Deterministic analysis of diagnostic JSON -> ranked findings, plus post-repair verification checks.

Findings are produced by fixed rules (source = "scripted"). They are NOT AI output.
"""
from __future__ import annotations

import json
from typing import Any, Callable

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def F(module: str, fid: str, severity: str, title: str, evidence: str, fix: list[tuple[str, dict]] | None = None) -> dict:
    return {"id": f"{module}.{fid}", "module": module, "severity": severity, "title": title,
            "evidence": evidence, "suggested": [{"tool_id": t, "params": p} for t, p in (fix or [])]}


def parse(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"device returned non-JSON output: {e}") from e


def _list(x: Any) -> list:
    return x if isinstance(x, list) else ([] if x is None else [x])


def a_cpu_ram(d):
    out = []
    if d["ram_percent"] >= 90:
        out.append(F("cpu_ram", "ram_high", "high", "Memory almost exhausted", f"RAM {d['ram_percent']}% used ({d['ram_used_gb']}/{d['ram_total_gb']} GB)"))
    if d["cpu_percent"] >= 90:
        out.append(F("cpu_ram", "cpu_high", "medium", "Sustained high CPU load", f"CPU {d['cpu_percent']}%"))
    return out


def a_performance(d):
    out = []
    if d.get("pending_reboot"):
        out.append(F("performance", "reboot_pending", "low", "A restart is pending", "Component servicing reports RebootPending"))
    if d.get("uptime_hours", 0) > 24 * 30:
        out.append(F("performance", "long_uptime", "low", "Machine has not restarted for over 30 days", f"Uptime {d['uptime_hours']} h"))
    return out


def a_startup(d):
    items = _list(d.get("items"))
    if len(items) <= 10:
        return []
    fix = [("disable_startup_item", {"name": i["name"]}) for i in items[10:13]]
    return [F("startup", "too_many", "medium", f"{len(items)} startup applications slow down boot",
              "; ".join(i["name"] for i in items[:8]) + " …", fix)]


def a_disk_health(d):
    out = []
    for k in _list(d.get("disks")):
        if k.get("health") != "Healthy" or k.get("operational") not in ("OK", None):
            out.append(F("disk_health", "unhealthy", "critical", f"Disk {k['model']} reports {k['health']}",
                         f"Health={k['health']} Operational={k['operational']}. Back up data immediately."))
    if d.get("smart_predict_failure"):
        out.append(F("disk_health", "smart", "critical", "SMART predicts imminent disk failure", "MSStorageDriver_FailurePredictStatus.PredictFailure=True"))
    return out


def a_windows_update(d):
    out = []
    svc = d.get("services", {})
    bad = [n for n, s in svc.items() if s != "Running"]
    fails = _list(d.get("failures"))
    if fails:
        out.append(F("windows_update", "failures", "high", f"{len(fails)} recent Windows Update failures",
                     fails[0].get("message", "")[:200], [("restart_update_services", {})]))
    elif bad:
        out.append(F("windows_update", "services", "medium", "Update services are stopped", f"Stopped: {', '.join(bad)}", [("restart_update_services", {})]))
    if (d.get("last_update_days") or 0) > 60:
        out.append(F("windows_update", "stale", "medium", "No updates installed for over 60 days", f"{d['last_update_days']} days"))
    return out


def a_drivers(d):
    p = _list(d.get("problem_devices"))
    return [F("drivers", "problem_devices", "medium", f"{len(p)} device(s) with driver problems",
              "; ".join(f"{x['name']} (code {x['error_code']})" for x in p))] if p else []


def a_network(d):
    out = []
    if not d.get("gateway_reachable"):
        out.append(F("network", "no_gateway", "critical", "Default gateway unreachable", "Ping to the gateway failed. Check cable, Wi-Fi, router."))
    elif not d.get("internet_reachable"):
        out.append(F("network", "no_internet", "high", "Gateway reachable but no Internet", "Ping to 1.1.1.1 failed.", [("reset_winsock", {})]))
    elif not d.get("dns_resolves"):
        out.append(F("network", "dns_fail", "high", "Internet works but DNS resolution fails", "Resolve-DnsName example.com failed", [("flush_dns", {})]))
    return out


def a_dns_dhcp(d):
    out = []
    for a in _list(d.get("adapters")):
        if a.get("apipa"):
            out.append(F("dns_dhcp", "apipa", "high", f"{a['name']} has a self-assigned (169.254.x.x) address",
                         f"IP {a['ip']}: no DHCP server answered.", [("renew_dhcp", {})]))
    if not d.get("dns_resolves") and not out:
        out.append(F("dns_dhcp", "dns", "medium", "DNS resolution failing", "Try flushing the cache.", [("flush_dns", {})]))
    return out


def a_printers(d):
    out = []
    if d.get("spooler") != "Running":
        out.append(F("printers", "spooler_down", "high", "Print Spooler is not running", f"Status: {d.get('spooler')}", [("restart_spooler", {})]))
    stuck = [p for p in _list(d.get("printers")) if p.get("jobs", 0) > 0 and p.get("status") not in ("Normal",)]
    if stuck:
        out.append(F("printers", "stuck_jobs", "medium", f"Stuck print jobs on {len(stuck)} printer(s)",
                     "; ".join(f"{p['name']}: {p['jobs']} jobs, {p['status']}" for p in stuck), [("clear_print_queue", {})]))
    return out


def a_event_log(d):
    out = []
    if d.get("critical", 0) > 0:
        out.append(F("event_log", "critical", "critical", f"{d['critical']} critical events in the last 24 h",
                     "; ".join(f"{s['source']} ×{s['count']}" for s in _list(d.get("top_sources")))))
    elif d.get("errors", 0) >= 50:
        out.append(F("event_log", "many_errors", "medium", f"{d['errors']} error events in the last 24 h",
                     "; ".join(f"{s['source']} ×{s['count']}: {s.get('sample','')[:60]}" for s in _list(d.get("top_sources")))))
    return out


def a_integrity(d):
    out = []
    if d.get("dism_checkhealth") == "repairable":
        out.append(F("integrity", "dism", "high", "Component store is corrupted but repairable", "DISM /CheckHealth: repairable", [("run_dism_restorehealth", {})]))
    if not d.get("defender_realtime"):
        out.append(F("integrity", "defender_off", "high", "Microsoft Defender real-time protection is off", "Security setting; changing it needs explicit approval.", [("enable_defender_realtime", {})]))
    if d.get("signature_age_days", 0) > 7:
        out.append(F("integrity", "sigs_old", "medium", "Antivirus signatures are out of date", f"{d['signature_age_days']} days old"))
    return out


def a_software(d):
    n = len(_list(d.get("apps")))
    return [F("software", "inventory", "info", f"{n} programs installed", "Review the inventory for unwanted software.")]


def a_storage(d):
    out = []
    for v in _list(d.get("volumes")):
        pct = 100 * v["free_gb"] / v["size_gb"] if v["size_gb"] else 100
        if pct < 10:
            fix = [("clear_temp_files", {})] if d.get("temp_mb", 0) >= 500 else []
            out.append(F("storage", "low_space", "high" if pct < 5 else "medium", f"Drive {v['drive']}: only {pct:.0f}% free",
                         f"{v['free_gb']} GB free of {v['size_gb']} GB; temp folder {d.get('temp_mb',0)} MB", fix))
    return out


ANALYZERS: dict[str, Callable[[dict], list[dict]]] = {
    "cpu_ram": a_cpu_ram, "performance": a_performance, "startup": a_startup, "disk_health": a_disk_health,
    "windows_update": a_windows_update, "drivers": a_drivers, "network": a_network, "dns_dhcp": a_dns_dhcp,
    "printers": a_printers, "event_log": a_event_log, "integrity": a_integrity, "software": a_software, "storage": a_storage,
}
MODULES = list(ANALYZERS)


def analyze(module: str, data: dict) -> list[dict]:
    return sorted(ANALYZERS[module](data), key=lambda f: SEV_ORDER[f["severity"]])


# ---- verification: (params, before, after) -> (ok, detail) ----
def _c(ok: bool, detail: str):
    return ok, detail


CHECKS: dict[str, Callable[[dict, dict, dict], tuple[bool, str]]] = {
    "dns_ok": lambda p, b, a: _c(bool(a["dns_resolves"]), f"dns_resolves={a['dns_resolves']}"),
    "no_apipa": lambda p, b, a: _c(not any(x.get("apipa") for x in _list(a["adapters"])), "no adapter on 169.254.x.x"),
    "spooler_running": lambda p, b, a: _c(a["spooler"] == "Running", f"spooler={a['spooler']}"),
    "no_stuck_jobs": lambda p, b, a: _c(sum(x.get("jobs", 0) for x in _list(a["printers"])) == 0, "queue empty"),
    "update_services_running": lambda p, b, a: _c(all(s == "Running" for s in a["services"].values()), f"services={a['services']}"),
    "startup_item_gone": lambda p, b, a: _c(p["name"] not in [i["name"] for i in _list(a["items"])], f"{p['name']} no longer starts"),
    "dism_healthy": lambda p, b, a: _c(a["dism_checkhealth"] == "healthy", f"dism={a['dism_checkhealth']}"),
    "temp_reduced": lambda p, b, a: _c(a["temp_mb"] < b["temp_mb"], f"temp {b['temp_mb']} MB -> {a['temp_mb']} MB"),
    "internet_ok": lambda p, b, a: _c(bool(a["internet_reachable"]), f"internet_reachable={a['internet_reachable']}"),
    "software_gone": lambda p, b, a: _c(p["name"] not in [i["name"] for i in _list(a["apps"])], f"{p['name']} not installed"),
    "defender_on": lambda p, b, a: _c(bool(a["defender_realtime"]), f"defender_realtime={a['defender_realtime']}"),
}
