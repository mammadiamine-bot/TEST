# First real LAN test — 2026-10-30

Main PC: Windows 11 (AMINE, 192.168.1.103). Second PC: Windows 10 (DESKTOP-1KV25VV, 192.168.1.105). Same Wi-Fi (TP-Link_420F, Private).
Agent: `cit-agent.exe` built locally from commit c54878f (CI run for that commit was stalled); its `--selftest` passed on the main PC. Copied to USB with SHA-256 verified.
Backend: run from an isolated venv (the system Python has fastapi 0.111.0 + starlette 1.8.0, which fail at startup). Pairing fingerprint was read from the live
certificate and confirmed by the owner against the Worker's screen before pairing (the fingerprint changes on every agent restart).

## Result of `tools/lan_check.py` (agent granted: terminal + write, root C:\CITWork; Claude and repair disabled)
| Result | Step | Evidence |
|---|---|---|
| PASS | controller reachable | HTTP 200 |
| PASS | agent port answers (single address, no scan) | open: agent |
| PASS | register device | dev_e65cc67317fb |
| PASS | pair with pinned TLS fingerprint + 8-digit code | status=paired |
| PASS | connection test | network/identity/auth/permissions ok; tools: git, node missing |
| PASS | device facts from the PC | host=DESKTOP-1KV25VV, os=windows, allowed=[terminal, write] |
| PASS | project folder inside authorized root | prj_770eb248f9d6 |
| PASS | remote `hostname` | DESKTOP-1KV25VV |
| PASS | remote `whoami` | desktop-1kv25vv\yaman |
| PASS | 200 KB file round trip | sha256 3ac37b644973d888… on both sides |
| **FAIL** | write hello.py remotely and run it | "Python was not found; run without arguments to install from the Microsoft Store" |
| SKIPPED | reconnect after real network interruption | needs a human to switch Wi-Fi off/on; not run |
| PASS | disconnect | session revoked, agent ended and stopped |

11 passed, 1 failed, 0 blocked, 1 skipped.

## Interpretation
- The FAIL is a missing prerequisite on the Worker, not a connection fault: the facts step listed `python` as present, but that is Windows' Microsoft Store stub
  (App Execution Alias), which does not run code. Real Python is not installed there. Installing it needs the owner's approval (not given yet).
  Product gap to fix: tool detection should run `python --version` instead of trusting the stub's presence on PATH.
- The facts step also lists `claude` as present while the first connection test said it was missing. Not verified; Claude Code on the Worker is untested.
- Not tested: cancellation, live logs, reconnect, Claude Code on the Worker, Worker Station (`cit-worker`), dashboard view, firewall script.
- Worker is NOT declared fully operational.
