# PROJECT_STATUS — Claude IT Technician

Last updated: 2026-10-30 · **M1** (MVP, mock devices) + **M2** (universal workspace) — both on branch `ccr-c7017ed0-jcu40v`, PR #2
**NOT production-ready.** No real client computer has been paired. Claude Code itself has never been run by this project's tests (a labelled test double stands in).

## Legend (also shown live in Settings → Feature status, from `backend/app/features.py`)
tested = automated tests pass · untested = code exists, never run against the real thing · mock = simulated only · planned = not built · blocked = needs something external

## M2 — what changed (everything from M1 is kept; 135 backend tests, up from 55)
| Area | Status | Evidence / caveat |
|---|---|---|
| Portable agent `agent/cit_agent.py` (TLS cert pinning, 8-digit single-use code, 5-attempt lockout, per-owner grants: terminal/write/repair/claude, session expiry, `d`+Enter / Ctrl+C disconnect, temp cert files deleted, refuses public source IPs, unknown JSON fields rejected so no credential field can exist) | **tested** | 16 loopback tests + `--selftest` (also run in CI on Linux and on a Windows runner) |
| Agent as a Windows `.exe` + USB folder (`start-agent.bat`, README) | `.exe` build + `--selftest` **passed on a Windows CI runner**; `start-agent.bat` and USB use **untested** | loopback only. Windows never auto-runs USB programs, so the client starts it |
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

## M4 — first real LAN test (main PC + second PC on the same Wi-Fi)
**Status: PARTLY PERFORMED on 2026-10-30 (real Windows 11 main PC + real Windows 10 second PC, same Wi-Fi): 11 PASS, 1 FAIL, 1 SKIPPED** — see `docs/LAN-TEST-REPORT-2026-10-30.md`. Verified for real: pinned-TLS pairing, remote hostname/whoami, 200 KB file round trip with SHA-256, disconnect. FAIL: Python task (Python not really installed on the Worker; Microsoft Store stub misdetected as present). SKIPPED: reconnect. Not tested: cancel, live logs, Claude Code on the Worker, Worker Station, dashboard. Agent .exe was a local build (CI run stalled); backend needed a venv because system fastapi 0.111/starlette 1.8 is incompatible.
(Older note, before this run:) No computer was connected to the authoring assistant's session and it ran in a Linux cloud container, so nothing was simulated.
What was built so you can do it and get real evidence:
- `tools/prepare-usb.ps1` — read-only network/USB listing, copy to `<drive>:\CITAgent` only after you type YES, never formats/deletes/overwrites, SHA-256 verified. **untested on Windows**; a static test forbids destructive commands.
- Agent banner now prints the PC's own LAN addresses (so no scanning is ever needed to find it). `probe` accepts the port you typed.
- `tools/lan_check.py` — the real end-to-end verifier (pair, connection test, remote exec, 200 KB file round trip with SHA-256, hello.py coding task, optional remote Claude with live log, real reconnect, disconnect). Proven against a real backend + real agent over **loopback only** (3 tests: all-pass, BLOCKED-not-success when the owner denied permissions, honest FAIL on a wrong fingerprint).
- Dashboard: *Devices → Details / test* shows the machine's own host/OS/tools/permissions and the staged connection test (backend `GET /devices/{id}/info`).
- `docs/LAN-TEST-RUNBOOK.md` — numbered steps, who does what, where your approval is required (firewall prompt, installs, Claude sign-in with a separate account).
**Still unverified until you run it:** every Windows-side behaviour of the above, real Wi-Fi reachability (router client isolation?), Claude Code on the second PC.

## M5 — Windows validation + hardening (2026-10-30, real Windows 11 main PC; evidence, not claims)
**Verified on a real Windows 11 PC:** frozen `cit-worker.exe` and `cit-agent.exe` build + `selftest` OK · `worker/windows_check.py` OK (DPAPI "OS-backed" secret round trip, enrollment, restart keeps identity, wipe removes data) · second worker on the same data dir is refused (exit 3, real exe) · new tests pass: boot-task command line, single-instance lock, exclusive port bind (Windows), dashboard states.
**Fixed (all found by running on Windows):** `windows_check.py` could never pass (dead assertion, banner order, `Popen.kill()` leaving the PyInstaller child alive) · frozen worker did not flush its banner/log when not on a console · boot task would have failed (`--data-dir` after `run`) and never bound the LAN address; it is now **at log on, interactive-only (`/IT`), no stored password**, takes `--bind/--port` · Windows `SO_REUSEADDR` let a second process share the agent/worker port → exclusive bind · `/disconnect` replied before the token was revoked and jobs stopped → revoke first, then reply · Python detection counted the Microsoft Store alias as installed → must run and print `Python x.y` · `start-agent.bat` dropped earlier flags (block-expansion bug) → effective permissions now match answers and are echoed before launch.
**Controller:** worker view now has `state` = online / reconnecting / offline / needs_pairing (backend tested; the UI label for `state` is not yet wired/verified).
**Real two-PC results (agent, LAN):** 11 PASS · Python task FAIL (Python then missing on the Worker; reinstalled by the owner, **not re-run**) · reconnect SKIPPED · cancel/live-log/Claude on the Worker/Worker Station on the 2nd PC **not tested**. no scheduled task has been installed anywhere.
**cit-worker on the real Windows 10 PC (manual mode, 2026-10-30): 13 PASS + controller-restart reconnect PASS, 0 FAIL** — enrollment with pinned fingerprint, hostname/whoami, 200 KB transfer with SHA-256 on both ends, workspace boundary refused (403), Python 3.12.10 task, Claude Code 2.1.314 present (version only), live log streaming, cancellation, audit. Evidence: `docs/WORKER-TEST-REPORT-2026-10-30.md`, `tools/worker_check.py`. Real Wi-Fi outage (~90 s): auto-reconnected by itself, credential still valid (PASS); controller took ~70 s to show `reconnecting` (open finding). **Reboot survival PASS** via a Startup-folder launcher (the scheduled task needs admin: `Access is denied`, UAC not bypassed): same fingerprint, old credential accepted, no manual start. Worker clock is 42 s ahead (finding). **Not run:** a Claude task, revoke/lock, startup task. **24/7 operation is NOT claimed.** The Worker keeps its data in `C:\CITWork\worker\data` (it has no configurable root like the agent's `--root`).
**Backend suite on Windows:** the failures are existing Linux-only test assumptions, identical with and without these changes where compared (api2 3, connections 1, security 1, servers 4: fake `ssh`/`sh` scripts, CRLF, DPAPI-off-Windows test); test_tasks (6) and test_worker (7) were not baseline-compared; workers_ctrl 2 (a 1 s timing assert vs Windows' ~2 s connect refusal, and a fake-`claude` shell script). CI for this commit never finished (stalled), so the committed `.exe`s are local builds.
**Not built yet (planned):** generic client/site/device model, RBAC, multi-OS adapters (WinRM/RDP/SSH plugin layer), Internet/VPN path, restore points — see docs/ARCHITECTURE-ASSESSMENT.md once written. Internet access and third-device control are NOT operational.

## M6 — multi-client foundation (2026-10-30)
**TESTED:** organizations → sites → devices/workers; scoped `technician`/`viewer` tokens (hash-only, revocable instantly); default-deny cross-client isolation in one choke point (`backend/app/tenancy.py`); audited denials; DB migration; per-client fleet view and onboarding steps; connection-path classification (lan/overlay/loopback). 12 new tests pass; auth-path regression (flow, security, api2, workers_ctrl) shows only the 6 known Windows-only failures. Live: real Win10 Worker assigned to a client+site on the running controller; a scoped token got its own fleet (200) and was denied global routes (403).
**IMPLEMENTED, not verified in a browser:** dashboard page *Clients & sites* (type-checked only).
**BLOCKED:** Internet/VPN access (needs your Tailscale/WireGuard accounts + a second network) — `docs/INTERNET-ACCESS.md`; real Claude Code task on the Worker (needs its own login + your approval); third-device control (no authorized device). **Observed 2026-10-30 evening:** the Worker PC was unreachable (50% ping loss, state `offline`) when checked after the controller restart — not diagnosed (PC asleep/Wi-Fi?).
**Worker terminal (2026-10-30 22:xx, real Win11 → real Win10):** Worker back online after power-on, `worker_check.py` 13/13 PASS again. Remote CMD (exit codes propagate, cwd = client workspace) and PowerShell 5.1 verified via the API and through the dashboard Terminal tab in a real Chromium (CMD run, PowerShell run, cancel of a running command, history, no auto-run on open). Terminal UI (`pages3.tsx`) fixed: removed a hardcoded hostname that auto-ran commands on open, repaired corrupted characters, added a CMD/PowerShell selector. Each run is a fresh process — not a persistent shell. Clients & sites page is still not browser-tested.

**PLANNED:** keyed audit chain, transport plugin interface/WinRM test, Linux/macOS, outbound relay, Windows restore points. See `docs/ARCHITECTURE-ASSESSMENT.md`.

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
