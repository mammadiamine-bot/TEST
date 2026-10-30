"""Honest feature matrix. Status vocabulary (shown in the UI):
tested = automated tests pass in this repo's CI (Linux/mock/loopback); untested = code exists, never run against the real thing;
mock = works only against simulated devices; planned = not built; blocked = needs something external."""

FEATURES = [
    # id, area, title, status, note
    ("tools.allowlist", "Core", "Allowlisted tool layer, risk classes, approvals, audit chain", "tested", "pytest"),
    ("diag.mock", "Diagnostics", "13 diagnostic modules + verified repairs on mock devices", "mock", "Rules tested; PowerShell scripts never run on real Windows"),
    ("diag.powershell", "Diagnostics", "PowerShell diagnostics/repairs on real Windows", "untested", "Needs a Windows test PC"),
    ("agent.core", "Agent", "Portable agent: TLS pinning, pairing code, grants, expiry, disconnect, confinement", "tested", "Loopback tests on Linux + CI selftest"),
    ("agent.windows", "Agent", "Agent .exe on Windows (PyInstaller build + --selftest in CI)", "untested", "CI job added; check Actions for result"),
    ("agent.usb", "Agent", "Run from USB drive (start-agent.bat)", "untested", "Windows does not auto-run USB programs by design"),
    ("agent.persistent", "Agent", "Persistent installed mode", "planned", "Only when explicitly requested; not built"),
    ("agent.gui", "Agent", "Graphical status window", "planned", "Console window is the visible indicator today"),
    ("claude.central", "Claude", "Central Claude Code on this computer (local mirror, diff, approved push, rollback)", "untested", "Machinery tested with a fake `claude`; real Claude never run here"),
    ("claude.remote", "Claude", "Remote Claude Code on the target via the agent (streaming, cancel, backup)", "untested", "Machinery tested with a fake `claude`; needs the client's own login"),
    ("claude.explain", "Claude", "Claude explains diagnostic findings", "untested", "Needs credentials + Internet"),
    ("dev.workspace", "Development", "Projects, explorer, editor save w/ backup, git, build/test, terminal, rollback", "tested", "On agent + local targets"),
    ("dev.deploy", "Development", "Deployment with typed production confirmation", "tested", "Local target only; no real server deploy yet"),
    ("server.tools", "Servers", "Allowlisted Linux admin actions + SSH target", "untested", "Quoting/gating tested through a fake ssh; never against a real server"),
    ("server.docker", "Servers", "Docker/nginx/systemd actions", "untested", "Argv built and gated; not run against real services"),
    ("server.sftp", "Servers", "SFTP-style file manager", "untested", "Implemented over ssh cat/ls (not the SFTP protocol)"),
    ("conn.probe", "Connections", "Port probe, staged connection test, troubleshooting hints", "tested", "Loopback"),
    ("conn.git", "Connections", "Git repository check (git ls-remote)", "tested", "Local file:// repo"),
    ("conn.tailscale", "Connections", "Tailscale peer list", "untested", "Parser tested on sample JSON; no tailnet here"),
    ("conn.rustdesk", "Remote desktop", "RustDesk launch (client approves on screen)", "untested", "Launch argv tested; RustDesk not installed here"),
    ("conn.winrm", "Connections", "WinRM-HTTPS transport", "untested", "Needs pywinrm + a Windows host"),
    ("conn.cloud_api", "Connections", "Cloud services / APIs", "planned", ""),
    ("conn.usb_cable", "Connections", "Direct USB cable", "blocked", "A USB cable provides no network or command channel; use Ethernet or the agent"),
    ("sec.dpapi", "Security", "Windows DPAPI credential storage", "untested", "Refuses to store secrets off Windows (tested)"),
    ("pkg.installer", "Packaging", "Tauri NSIS installer", "untested", "Built green in CI; never installed or run"),
    ("pkg.signing", "Packaging", "Code signing", "blocked", "Needs a code-signing certificate"),
    ("worker.core", "Worker Station", "Persistent worker service: pinned-TLS identity, enrollment code, revocable credentials, authorization window, lock/STOP file/emergency stop", "tested", "26 worker tests incl. real process SIGKILL + restart"),
    ("worker.isolation", "Worker Station", "Per-client workspace, secrets, Claude config dir; no cross-client file/env access; approved-systems-only network checks", "tested", "Linux; DPAPI path untested"),
    ("worker.queue", "Worker Station", "Durable task queue: approval, production confirmation, checkpoints, pause/resume (Claude session), cancel, crash recovery that never re-runs destructive work", "tested", "with the fake `claude` double"),
    ("worker.autonomy", "Worker Station", "Approved tasks keep running when the laptop disconnects; history replayable on reconnect", "tested", "loopback"),
    ("worker.reconnect", "Worker Station", "Online/offline monitor, GET retries with backoff, automatic reconnection, identity-mismatch refusal", "tested", "loopback; real Tailscale flaps untested"),
    ("worker.dpapi", "Worker Station", "Secrets + TLS key protected by Windows DPAPI", "untested", "CI step windows_check.py runs it on a Windows runner — check the result"),
    ("worker.windows", "Worker Station", "Worker .exe on Windows + scheduled-task install (`service install --i-understand`)", "untested", "Command strings tested; never run on Windows"),
    ("worker.firewall", "Worker Station", "Generated Windows Firewall script isolating the client's LAN", "untested", "CIDR maths tested; script never applied"),
    ("worker.tailscale", "Worker Station", "Reaching the worker over Tailscale (bind to 100.x)", "untested", "Needs a real tailnet"),
    ("worker.ssh", "Worker Station", "SSH to the worker as a second command channel", "planned", "The pinned-TLS worker API is the control channel today"),
    ("worker.remote_claude", "Worker Station", "Claude Code on the worker with each client's own key", "untested", "Needs real Claude + a client key"),
    ("ui.i18n", "UI", "Arabic/English with RTL", "tested", "Navigation + main actions; some long texts still English"),
]


def matrix():
    return [dict(zip(("id", "area", "title", "status", "note"), f)) for f in FEATURES]
