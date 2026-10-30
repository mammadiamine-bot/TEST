# Architecture assessment — universal multi-client CIT (2026-10-30)

Labels: **TESTED** (automated test and/or run on real hardware, evidence named) · **IMPLEMENTED** (code exists, not verified on the real thing) · **BLOCKED** (needs something only the owner can supply) · **PLANNED** (not built). Nothing here is marked operational without evidence.

## 1. What exists and is reused (nothing was rebuilt)
| Layer | Component | Status |
|---|---|---|
| UI | React/TS dashboard (`frontend/`), AR/EN | TESTED (type-check + vitest). New **Clients & sites** page: IMPLEMENTED, type-checked, **not yet opened in a browser** |
| Orchestration | FastAPI controller `backend/app` (tasks, plans, approvals, audit) | TESTED (suite; ~26 known Linux-only test assumptions fail on Windows, none in changed code) |
| Transport / identity | Pinned-TLS pairing (agent, worker), 8-digit single-use code, fingerprint compared by the owner | TESTED on real Win11 ↔ Win10 over Wi-Fi |
| Device management | Targets: agent, local, SSH (Linux). Allowlisted tools, confined filesystem, argv-only exec | agent/local TESTED on real PCs; SSH TESTED only against a fake `ssh` |
| Persistent execution | `cit-worker`: stable identity, DPAPI secret store, revocable credential, per-client workspaces, queue, cancel, checkpoints | TESTED on the real Win10 PC (13 checks, controller restart, Wi-Fi outage, reboot) |
| Audit | SHA-256 hash chain in SQLite | TESTED; **unkeyed** — detects accidental edits only, not a deliberate rewrite by someone with DB access |
| Claude Code | Central/remote/worker task kinds, per-client config dir | machinery TESTED with a test double; **real Claude task on the Worker: BLOCKED** (needs the Worker's own login and your approval) |

## 2. Added today: Client → Site → Device
* Tables `orgs`, `sites`, `principals`; nullable `org_id/site_id` on `devices` and `workers` (idempotent migration, old rows stay owner-only).
* Roles: built-in owner token (unchanged); `technician` (read/write inside own clients); `viewer` (read-only). Tokens are `cit_…`, shown once, stored as SHA-256 only, revocable with immediate effect.
* **Default-deny** in the single choke point `tenancy.authorize`: a scoped principal reaches a resource only if its organization is in the principal's list; global routes (`/devices`, `/audit`, `/workers`, `/principals`, emergency-stop…) and unassigned resources are owner-only; "forbidden" and "does not exist" give the same 403 (no probing); every denial is audited with the principal id.
* API: `/orgs`, `/orgs/{id}/sites|devices|workers|fleet|onboarding`, `PUT /devices|workers/{id}/assign`, `/principals`.
* Evidence: 12 tests (`tests/test_tenancy.py`) incl. cross-client isolation in every route shape, no owner escalation, hash-only tokens, audit chain still verifies, DB migration. Live check on the running controller with the real Win10 Worker assigned to a client: scoped token → own fleet 200, `/devices` 403.
* Residual risk: isolation of *data and API access* is enforced in the controller; the Worker itself still runs all its clients as one Windows user (use separate Windows accounts/Workers for hostile clients). The controller trusts its own SQLite file — the audit chain is not tamper-proof against a local admin.

## 3. Requirement coverage
| Requirement | State |
|---|---|
| Multiple clients/sites/devices, central management | IMPLEMENTED + TESTED (API); dashboard page IMPLEMENTED, not browser-verified |
| Enrollment, device identity, revoke | TESTED (worker/agent); org-level onboarding steps endpoint IMPLEMENTED |
| Several Workers at once | IMPLEMENTED (list/poll/proxy per worker); only **one** real Worker exists → multi-Worker concurrency **not tested on hardware** |
| Windows 10/11 | TESTED (Win10 Worker, Win11 controller) |
| Windows Server, Linux, macOS | Linux over SSH IMPLEMENTED (fake-ssh tests); Windows Server, macOS **PLANNED**. Worker/agent are Python/PyInstaller so Linux builds are expected to work but are **untested** |
| LAN | TESTED |
| Internet | **NOT operational — BLOCKED** (needs your Tailscale/WireGuard accounts and a second network). See `INTERNET-ACCESS.md` |
| Third-party device control (a client PC through a Worker) | **NOT tested** (no authorized third device) |
| Always-on Worker | Starts at logon via Startup folder: reboot TESTED once. Auto-logon not configured; multi-day uptime **not tested** |
| Restore points before destructive operations | Project checkpoints TESTED (tar.gz); Windows system restore points **PLANNED** |
| WinRM/RDP/remote-desktop adapters | WinRM-HTTPS transport written, UNTESTED; RustDesk launch only; adapter/plugin interface **PLANNED** (transports are already a small class interface in `backend/app/transports`) |
| Claude Code remote diagnostics/scripts/development | see §1 — BLOCKED on real login |

## 4. Phased plan
1. **Done:** tenancy foundation, worker persistence, LAN validation.
2. **Next (needs owner):** install Tailscale on the Main PC and the Worker (you sign in) → run the Internet test in `INTERNET-ACCESS.md`; sign Claude Code in on the Worker with the separate account → run one real Claude task; revoke/lock drill.
3. **Then (no owner needed):** browser e2e for the Clients page; keyed (HMAC) audit chain; transport plugin interface + WinRM test against a Windows VM; Linux Worker build; Windows restore-point step in the plan engine.
