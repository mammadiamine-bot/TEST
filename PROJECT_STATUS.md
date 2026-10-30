# PROJECT_STATUS — Claude IT Technician

Last updated: 2026-10-30 · **M1** (MVP, mock devices) + **M2** (universal workspace) — both on branch `ccr-c7017ed0-jcu40v`, PR #2
**NOT production-ready.** No real client computer has been paired. Claude Code itself has never been run by this project's tests (a labelled test double stands in).

## Legend (also shown live in Settings → Feature status, from `backend/app/features.py`)
tested = automated tests pass · untested = code exists, never run against the real thing · mock = simulated only · planned = not built · blocked = needs something external

## M2 — what changed (everything from M1 is kept; 135 backend tests, up from 55)
| Area | Status | Evidence / caveat |
|---|---|---|
| Portable agent `agent/cit_agent.py` (TLS cert pinning, 8-digit single-use code, 5-attempt lockout, per-owner grants: terminal/write/repair/claude, session expiry, `d`+Enter / Ctrl+C disconnect, temp cert files deleted, refuses public source IPs, unknown JSON fields rejected so no credential field can exist) | **tested** | 16 loopback tests + `--selftest` (also run in CI on Linux and on a Windows runner) |
| Agent as a Windows `.exe` + USB folder (`start-agent.bat`, README) | **untested** | CI job `agent-windows` builds it and runs `--selftest`; check the Actions result. Windows never auto-runs USB programs, so the client starts it |
| Targets: one API for agent, this computer, Linux SSH servers; confined filesystem (symlink-safe), argv-only exec, scrubbed env, capped output, cancel/timeout | **tested** | agent + local; SSH via a fake `ssh` that executes the remote string through `sh` (proves quoting) |
| Existing Windows diagnostics/repairs now also run through the agent | **untested** on Windows | routed + grant-checked in tests with an injected PowerShell runner |
| Dev workspace: projects, explorer, editor (keeps previous version on every save), git status/diff, build/test, restricted terminal, backup/rollback, environment info | **tested** | agent + local targets |
| Task engine: identify target → verify authorization+connection → detect tools → choose Central/Remote → plan → approvals → live events → verify → report; kinds dev / diagnose / install_tools / server / deploy | **tested** machinery | with the `fake_claude` double |
| Central Claude (Claude Code on this laptop against a local mirror; diff review; approved push with read-back verification; rollback) | **untested** with real Claude | needs `claude` installed + logged in on this laptop |
| Remote Claude (Claude Code on the client PC through the agent; project backed up first; streamed output; cancel; client's own login) | **untested** with real Claude | needs `--allow-claude` on the agent and the client to log in themselves |
| Server manager: 19 allowlisted Linux actions (systemd/docker/nginx/logs/network/backup), sudo -n for root, typed-name confirmation for production changes | **untested** on a real server | quoting/gating tested; read-only actions executed for real on this Linux box through the fake ssh |
| Connections: port probe, staged connection test with troubleshooting hints, git check (real `git ls-remote` on a local repo), Tailscale peers, RustDesk launch (no password on CLI) | probe/git **tested**; Tailscale/RustDesk **untested** | no tailnet / RustDesk here |
| Public SSH only for `kind=server` + explicit acknowledgement; never for agent/WinRM | **tested** | |
| Emergency stop (revoke sessions, disconnect agents, cancel jobs) | **tested** | API, UI button, real agent process stopped in the browser e2e |
| UI: 14 sections (all M1 pages still reachable as tabs), AR/EN + RTL, task view with approvals/live log/diff | **tested** | `e2e/universal.js`: real browser + real backend + real agent process + test-double Claude; found and fixed a real bug (API rejected the agent's 8-digit code) |
| Windows installer | **untested** | CI built the NSIS installer green (M1); never installed or run |


## M3 — Dedicated AI Worker Station (added on top of M1/M2; nothing removed)
Main laptop → pinned-TLS connection (Tailscale/LAN) → **Worker PC** (`worker/cit_worker.py`) → per-client workspaces/projects/approved systems.
| Requirement | Status | Evidence / caveat |
|---|---|---|
| Worker registration, enrollment code shown on the worker, credential revocable, optional expiry, several workers | **tested** | enrollment rules, single use, 5-attempt cancel, revoke (credential and `revoke-all`) |
| Online/offline, CPU/RAM/disk, active tasks, queue counts in the dashboard | **tested** (Linux) | psutil; Windows readings untested |
| Assign worker to client/project (clients with own workspace, approved systems, authorization days) | **tested** | |
| Claude Code on the worker, per-client sessions, streaming logs, reconnect-and-replay, cancel, emergency stop | **tested machinery**; real Claude **untested** | fake `claude` double; each client gets its own `CLAUDE_CONFIG_DIR` and its own `ANTHROPIC_API_KEY` secret; the owner's key is scrubbed |
| Keeps running approved tasks when the laptop is gone | **tested** | |
| Automatic reconnection after network interruption | **tested** | GETs retried with backoff; POSTs never blindly retried; a different certificate on the same address is refused |
| Durable queue: pending/running/completed/failed/paused/cancelled/interrupted/needs_review | **tested** | one running task per project; global parallelism configurable |
| Safe resume; never repeat destructive actions | **tested** | real SIGKILL test: a destructive `command` that was running becomes `needs_review`, is NOT re-run, orphan process is killed; only `retry` with a new approval can run it again; `auto_resume` is opt-in and only for non-destructive Claude tasks with a saved session |
| Production approval | **tested** | deploy is always production: typed "Client name/project" |
| Automatic checkpoints + restore | **tested** | tar.gz snapshot before Claude/destructive steps (100 MB cap, refuses to run without one) |
| Client-site mode: approved systems only, no scanning, visible identity/status, revoke, remove | **tested** | `system-check` refuses anything not on the client's list; STOP file; authorization window locks the worker when it ends; `wipe --name` overwrites secrets then deletes |
| Network isolation / firewall | **untested** | generates a Windows Firewall script (private ranges minus approved systems/gateway/DNS) — never applied; app-level policy only until you run it |
| Terminal / files / git / build / test / deploy per client | **tested** | Docker availability is only detected, no Docker-specific actions yet |
| Tailscale / SSH / RustDesk | Tailscale **untested**; RustDesk launch **untested**; SSH **planned** for the worker | worker API listens only for private/Tailscale sources and is meant to be bound to the 100.x address |
| Secrets in OS-backed storage | **untested** on Windows | DPAPI via `windows_check.py` in CI; elsewhere the worker REFUSES to store secrets unless started with `--dev-insecure-store` (reported as "NOT OS-backed") |
| Visible, explicit persistence | **untested** | `service install --user X --i-understand` creates one named scheduled task; `service remove` deletes it; no hidden persistence |
| Dashboard section "AI Worker Stations" (overview, add wizard, clients/projects, queue, live log, terminal, remote desktop, security) | **tested** | browser e2e `e2e/worker.js` |

Limits to remember: (1) client isolation is enforced by the worker's code (separate folders, env, secrets); a malicious *project* running as the same Windows user could still read other clients' folders — for hostile clients use separate Windows accounts or separate Workers. (2) Claude Code's own network/file access is not sandboxed by this app; the firewall script and a dedicated Windows account are the real controls. (3) The controller's token for a worker is persisted only where DPAPI exists; elsewhere you re-enroll after restarting the app.

## Known gaps / blocked (honest list)
- **Real Claude never exercised**: the flag set (`-p --output-format stream-json --verbose --permission-mode acceptEdits --allowedTools …`) is from documentation knowledge; first run on a real login may need adjustments. Central-mode isolation between clients relies on a per-device/per-project working directory + restricted tools, not an OS sandbox.
- Prompt-injection risk: project files can contain text aimed at Claude. Mitigations: file-edit-only tools by default (Bash off), approvals before anything is pushed, backups, verification. Not eliminated.
- Remote desktop: RustDesk is launched, not embedded; Tailscale is read-only (peer list). Cloud-service/API connections: planned.
- Persistent installed agent and graphical agent window: planned (console window is the visible indicator).
- Agent session token is memory-only: if this app restarts, the client must pair again (no silent reconnect). Running jobs stay on the agent until the session ends.
- Windows-only pieces (PowerShell scripts, DPAPI store, SSH/WinRM to Windows, installer run, signing) still need a real Windows test PC. Code signing needs a certificate.
- Server SFTP is `ssh cat/ls`, not the SFTP protocol. No Windows Server management beyond the existing PowerShell tools.
- Single local API token; no per-technician accounts.
- HawkScan DAST not run (no `HAWK_API_KEY`).

## What I need from you to move to M3
1. A Windows test PC you own (or a VM) with the agent copied over → I can verify the PowerShell scripts, the agent .exe and the installer.
2. `claude` installed and signed in on this laptop (for Central) and, on a test client, signed in with the client's own account (for Remote).
3. Optionally one test Linux VPS (key-only SSH) for server actions.

## Environment (authoring)
Linux cloud container. Python 3.13, Node 22, Rust 1.97. **No Windows, no PowerShell, no WebView2** → Windows-specific parts could not be executed.
`C:\Users\AMINE\.claude\skills` is not reachable from this container; only the repo's `.claude/skills` (ui-ux-pro-max etc.) exist.

## Verified (run in this environment)
| Item | Evidence |
|---|---|
| Backend: 55 pytest tests pass | `cd backend && python -m pytest -q` |
| Frontend: type-check + production build + 1 vitest | `cd frontend && npm test && npm run build` |
| End-to-end UI flow against the real backend with a mock device (register → pair → consent → diagnose → plan → approve → execute → verified → re-scan, then Arabic/RTL switch) | Playwright run, 0 console errors, 8 steps shown "verified", `dir=rtl` |
| Example PDF reports generated | `docs/examples/` (from mock data) |

## Implemented
- **Tool layer**: 13 read-only diagnostics + 11 repairs + restricted console in an allowlist (`backend/app/tools.py`); risk classes; strict param validation; params travel as base64 JSON (never interpolated); timeouts; single execution choke point (`Core._exec`).
- **Authorization**: owner/authorization record, one-time pairing code (expiry, 5-attempt throttle), SSH host-key fingerprint pinning, consent codes, time-boxed sessions (scope `diagnose`/`repair`), revoke device/session, access denied + audited when no active session.
- **Network policy**: only RFC1918, link-local (Ethernet), loopback, Tailscale 100.64/10, IPv6 ULA/link-local. Public addresses rejected (test-net ranges too).
- **Diagnostics (13 modules)** with rule-based ranked findings, labelled `scripted`.
- **Repairs**: plan from findings → per-step approval; high-risk needs explicit per-action confirmation (bulk approve skips them); approval hash detects tampering; before/after capture; **verification re-reads the device**; result is `verified` / `unverified` / `failed` / `blocked` — never "success" without a passing check.
- **Audit**: SQLite hash-chained log (tamper-evident), PDF reports (findings, actions, outcome, re-scan, notes), technician notes.
- **Offline mode**: connectivity probe; diagnostics keep working; chat answers are labelled "scripted"; sanitized question + finding ids queued locally.
- **UI**: dark-blue React/TS dashboard, 15 pages, AR/EN with RTL.
- **Transports**: Mock (tested); OpenSSH and WinRM-HTTPS written but **untested**.
- **Packaging files**: Tauri v2 shell + GitHub Actions CI. CI run 1: backend and frontend jobs green on GitHub; the Windows job built the PyInstaller sidecar, then failed in `tauri build` (CLI run from the wrong directory) — fixed in follow-up, installer still unverified.

## Untested / unverified
- All PowerShell scripts (syntax, output shape, behaviour on Windows 10/11, elevation needs).
- SSH and WinRM transports against a real host; DPAPI credential store (`security.py`).
- `ClaudeProvider` (`ai.py`) — needs Claude credentials + Internet.
- Tauri shell compile, sidecar spawn, NSIS installer, code signing (no certificate).
- Windows-side OpenSSH/WinRM setup instructions (documented, not exercised).

## Blocked / not built
- **Real authorized target**: none paired. Needed from you: a test PC you own with OpenSSH Server (or WinRM-HTTPS) enabled, its private/Tailscale IP, and a key pair.
- **Client-side consent agent** (signed, visible): not built. Until it exists, for real devices consent is *attested by the technician* (consenting person recorded in the audit log); the mock flow shows the intended code-confirmation protocol.
- **RustDesk and Tailscale integration**: UI text only; no launch/status code.
- **Claude-driven planning**: Claude only *explains* findings (no tool access). Plans are rule-based from the allowlist.
- **Client registration** is embedded in the device record (no separate clients table/CRUD).
- Offline queue has no review/flush UI. Arabic: navigation and main actions translated; many table headers and long texts still English. PDF is English only (Arabic shaping needs a bundled font).
- Device discovery (LAN scan) is manual entry only.
- Windows Update analyzer keeps flagging historical failure events after a repair (honest but noisy).
- Identity check: for SSH, protection is `StrictHostKeyChecking=yes` against the pinned key; `Core._exec`'s fingerprint comparison is only meaningful for transports that report a live fingerprint (mock). WinRM relies on CA validation, no key pinning yet.
- HawkScan DAST not run (no `HAWK_API_KEY`).
- Single local API token; no per-technician accounts/roles.

## Next milestone (M2) — needs a Windows test PC
1. Run each PowerShell script on a real Windows VM; fix shapes; add golden-output parser tests.
2. Pair a real device over SSH; run read-only diagnostics only.
3. Build the sidecar + installer on Windows; smoke test.
4. Client consent agent; RustDesk/Tailscale status checks.

## Change log
- 2026-10-30 M1: backend, UI, tests, docs, CI/Tauri scaffolding created from scratch (repo was empty apart from design skills).
