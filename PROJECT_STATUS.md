# PROJECT_STATUS — Claude IT Technician

Last updated: 2026-10-30 · Milestone **M1 (MVP, mock devices only)**
**NOT production-ready.** Nothing here has run against a real Windows computer.

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
