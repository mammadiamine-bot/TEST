"""Connection manager: detect what a machine offers, test a connection stage by stage, and say how to fix it."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path

from .netpolicy import NetworkPolicyError, assert_private_address

METHODS = [
    {"id": "ethernet", "title": "Direct Ethernet", "kind": "network",
     "steps": ["Plug an Ethernet cable between the two computers (a USB cable does NOT create a network or command access).",
               "Give both a static address on 169.254.10.0/24, e.g. 169.254.10.1 and 169.254.10.2, mask 255.255.0.0, no gateway.",
               "Start the agent on the client (or enable OpenSSH Server) and use the client's 169.254.x.x address here."]},
    {"id": "lan", "title": "Local Wi-Fi / LAN", "kind": "network",
     "steps": ["Both computers on the same network.", "Use the client's private address (10.x, 172.16-31.x, 192.168.x).", "Then pick agent, SSH or WinRM below."]},
    {"id": "usb_agent", "title": "Portable USB Technician Agent", "kind": "agent",
     "steps": ["Copy the agent folder from the technician USB drive to the client (or run it from the drive).",
               "Windows does not auto-run USB programs: the client double-clicks start-agent.bat and chooses what to allow.",
               "Read the pairing code and fingerprint from the client's screen into Quick Connect."]},
    {"id": "ssh", "title": "Secure SSH", "kind": "ssh", "steps": ["Enable OpenSSH Server on the target.", "Add your public key; disable password login.", "Verify the host-key fingerprint with the owner."]},
    {"id": "winrm", "title": "PowerShell Remoting (WinRM-HTTPS)", "kind": "winrm", "steps": ["Enable WinRM over HTTPS (5986) with a certificate; never plain HTTP over untrusted networks."]},
    {"id": "tailscale", "title": "Tailscale private network", "kind": "overlay", "steps": ["Install Tailscale on both machines and sign in to YOUR tailnet.", "Use the 100.x address or MagicDNS name."]},
    {"id": "rustdesk", "title": "RustDesk remote desktop", "kind": "desktop", "steps": ["Client installs/starts RustDesk and gives you the ID.", "The client approves the incoming connection on screen."]},
    {"id": "vps", "title": "Linux / Windows VPS", "kind": "ssh", "steps": ["SSH with a key to your own server.", "Create a non-root user; use sudo only for approved actions."]},
    {"id": "git", "title": "Git repository", "kind": "git", "steps": ["Provide the repository URL (https or ssh); access uses your existing git credentials."]},
    {"id": "cloud_api", "title": "Cloud services / APIs", "kind": "api", "steps": ["Planned: store API tokens in OS-protected storage and run read-only checks."]},
]

PORTS = [(8766, "agent", "Technician agent"), (22, "ssh", "OpenSSH"), (5986, "winrm-https", "WinRM (HTTPS)"), (5985, "winrm-http", "WinRM (HTTP, discouraged)"),
         (3389, "rdp", "Windows Remote Desktop"), (21118, "rustdesk", "RustDesk direct")]

HINTS = {
    "network": "Check the cable/Wi-Fi, that both computers are on the same subnet, and that the client's firewall allows the port.",
    "identity": "The fingerprint differs from the one the owner confirmed. Do NOT continue: it could be another computer. Ask the owner to read it again.",
    "auth": "The pairing code is single-use and expires after 10 minutes; restart the agent for a new code.",
    "permission": "The owner did not grant this capability. Restart the agent with the needed --allow-* options.",
    "tools": "Run the 'Install tools' task (needs approval) or install the missing tools manually.",
}


def _tcp(host: str, port: int, timeout=0.8) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def probe(address: str, allow_public: bool = False) -> dict:
    """Which connection methods does this address offer? TCP connect only: no credentials, no requests."""
    host = address.rsplit(":", 1)[0] if re.search(r":\d+$", address) and address.count(":") == 1 else address
    if not allow_public:
        assert_private_address(host)
    found, lock = [], threading.Lock()

    def one(p):
        if _tcp(host, p[0]):
            with lock:
                found.append({"port": p[0], "method": p[1], "label": p[2]})
    ths = [threading.Thread(target=one, args=(p,)) for p in PORTS]
    [t.start() for t in ths]; [t.join() for t in ths]
    found.sort(key=lambda f: [p[0] for p in PORTS].index(f["port"]))
    advice = []
    if any(f["method"] == "agent" for f in found):
        advice.append("Technician agent detected — pair with the code and fingerprint from the client's screen.")
    if any(f["method"] == "ssh" for f in found):
        advice.append("SSH is open — use key authentication and verify the host key.")
    if any(f["method"] == "winrm-http" for f in found) and not any(f["method"] == "winrm-https" for f in found):
        advice.append("Only plain-HTTP WinRM is open: prefer HTTPS or SSH.")
    if any(f["method"] == "rdp" for f in found):
        advice.append("RDP is open on the LAN; for support use RustDesk with the client's approval, and do not expose RDP to the Internet.")
    if not found:
        advice.append("Nothing answered. A USB cable alone provides no network: use Ethernet/LAN, or start the agent on the client.")
    return {"host": host, "open": found, "advice": advice}


def local_capabilities() -> dict:
    """What THIS computer can do: ssh client, tailscale, rustdesk, git, claude."""
    def has(*names):
        return next((p for n in names if (p := shutil.which(n))), None)
    rd = has("rustdesk") or next((p for p in (r"C:\Program Files\RustDesk\rustdesk.exe",) if os.path.exists(p)), None)
    return {"ssh": bool(has("ssh")), "git": bool(has("git")), "claude": bool(has("claude")), "tailscale": bool(has("tailscale")),
            "rustdesk": bool(rd), "powershell": bool(has("powershell", "pwsh")), "docker": bool(has("docker"))}


def tailscale_peers(runner=None) -> dict:
    """Parse `tailscale status --json`. `runner` is injectable for tests."""
    def default():
        p = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout
    try:
        rc, out = (runner or default)()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "tailscale CLI not found or not responding", "peers": []}
    if rc != 0:
        return {"available": False, "reason": "tailscale is not logged in or not running", "peers": []}
    j = json.loads(out)
    peers = [{"name": v.get("HostName"), "ip": (v.get("TailscaleIPs") or [None])[0], "online": v.get("Online"), "os": v.get("OS")}
             for v in (j.get("Peer") or {}).values()]
    return {"available": True, "backend_state": j.get("BackendState"), "peers": sorted(peers, key=lambda p: str(p["name"]))}


_RD_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


def rustdesk_launch(remote_id: str, popen=None) -> dict:
    """Open RustDesk towards an ID. No password is ever passed on the command line; the client approves on their screen."""
    if not _RD_ID.match(remote_id):
        raise ValueError("invalid RustDesk ID")
    exe = shutil.which("rustdesk") or (r"C:\Program Files\RustDesk\rustdesk.exe" if os.path.exists(r"C:\Program Files\RustDesk\rustdesk.exe") else None)
    if not exe:
        return {"launched": False, "reason": "RustDesk is not installed on this computer", "install": "https://rustdesk.com"}
    (popen or subprocess.Popen)([exe, "--connect", remote_id], shell=False)
    return {"launched": True, "note": "Ask the client to accept the incoming connection on their screen."}


_GIT_URL = re.compile(r"^(https://[\w.-]+/[\w./~%-]+|git@[\w.-]+:[\w./~-]+|ssh://[\w.@-]+/[\w./~-]+|file:///[\w./-]+)$")


def git_check(url: str) -> dict:
    """`git ls-remote` — read-only, no prompts, short timeout. Uses the credentials the technician's git already has."""
    if not _GIT_URL.match(url) or url.startswith("-"):
        return {"ok": False, "detail": "unsupported or malformed URL (https, git@host:path, ssh:// or file:///)"}
    if not shutil.which("git"):
        return {"ok": False, "detail": "git is not installed on this computer"}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"}
    try:
        p = subprocess.run(["git", "ls-remote", "--heads", "--", url], capture_output=True, text=True, timeout=25, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "timed out", "hint": "check the network or that the host is reachable"}
    if p.returncode != 0:
        return {"ok": False, "detail": p.stderr.strip()[-300:], "hint": "check the URL and that your git credentials/SSH key have access"}
    heads = [l.split("refs/heads/")[-1] for l in p.stdout.splitlines() if "refs/heads/" in l]
    return {"ok": True, "detail": f"{len(heads)} branch(es) readable", "branches": heads[:20]}


def agent_test(core, did: str) -> dict:
    """Staged connection test for a paired device: network → identity → authentication → permissions → tools."""
    d = core.device(did)
    checks = []

    def add(stage, ok, detail, hint_key=None):
        checks.append({"stage": stage, "ok": ok, "detail": detail, "hint": None if ok else HINTS.get(hint_key or stage)})
        return ok
    if d["transport"] == "local":
        add("network", True, "this computer"); add("identity", True, "this computer"); add("authentication", True, "owner is the technician")
        info = core.local_target().info()
        add("permissions", True, f"grants: {', '.join(info['grants'])}")
        add("tools", True, ", ".join(k for k, v in info["tools"].items() if v["found"]) or "none")
        return {"device": did, "ok": True, "checks": checks}
    host, _, port = d["address"].partition(":")
    port = int(port or {"agent": 8766, "ssh": 22, "winrm": 5986}.get(d["transport"], 0) or 0)
    if d["transport"] == "mock":
        add("network", True, "simulated"); add("identity", True, "simulated fingerprint")
        add("authentication", d["status"] == "paired", d["status"], "auth")
        return {"device": did, "ok": d["status"] == "paired", "mock": True, "checks": checks}
    if not add("network", _tcp(host, port, 2), f"{host}:{port}", "network"):
        return {"device": did, "ok": False, "checks": checks}
    if d["status"] != "paired":
        add("authentication", False, "not paired", "auth"); return {"device": did, "ok": False, "checks": checks}
    try:
        t = core.target(did)
        info = t.info()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        add("identity", "identity mismatch" not in msg, msg, "identity")
        add("authentication", "identity mismatch" in msg, msg, "auth")
        return {"device": did, "ok": False, "checks": checks}
    add("identity", True, f"fingerprint {d['fingerprint'][:16]}… verified on this connection")
    add("authentication", True, "session token accepted")
    g = info.get("grants") or []
    add("permissions", True, f"owner allowed: {', '.join(g) or 'read-only diagnostics only'}")
    miss = [k for k in ("git", "node", "python", "claude") if not (info.get("tools") or {}).get(k, {}).get("found")]
    add("tools", not miss, ("missing: " + ", ".join(miss)) if miss else "git, node, python, claude present", "tools")
    return {"device": did, "ok": all(c["ok"] for c in checks if c["stage"] != "tools"), "checks": checks, "info": info}
