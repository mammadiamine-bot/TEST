# Architecture

```
Tauri shell (Rust)  ──env CIT_API_TOKEN──▶  Python sidecar (FastAPI, 127.0.0.1:8765, SQLite)
   │  IPC backend_config                         │
   ▼                                             ├── Core (service.py): authorization, sessions, plans, approvals, verification
React/TS UI ──Bearer token──────────────────────▶ ├── tools.py: allowlist registry (the only thing that can touch a device)
                                                 ├── diagnostics.py: parsers → ranked findings, verification checks
                                                 ├── transports/: mock | ssh | winrm
                                                 ├── ai.py: Claude explains redacted findings; scripted fallback
                                                 └── db.py (SQLite + hash-chained audit), reports.py (PDF)
```

## Rules the code enforces
1. A device is touched only through `Core._exec`, which requires: paired device → active, unexpired, client-consented session of sufficient scope → allowlisted tool → validated params → (identity check) → audited run.
2. Tools are fixed scripts. Params are base64 JSON decoded inside PowerShell. The console accepts only a single allowlisted `Get-*` cmdlet with simple tokens.
3. Risk: `read_only` (diagnostics) · `low` (service restarts, DNS/DHCP, startup item, DISM) · `high` (deleting files, network reset, uninstall, security setting). High needs per-step confirmation; bulk approval never includes it. Disk partitioning is deliberately **absent** from the registry.
4. A repair is reported `verified` only if a re-read diagnostic satisfies its check (`diagnostics.CHECKS`). Otherwise `unverified`/`failed`.
5. Targets must be on private/link-local/Tailscale space.
6. Cloud Claude never receives raw evidence: IPs, MACs, e-mails and `C:\Users\<name>` are redacted; it has no tools. Offline → deterministic answers explicitly labelled "scripted".

## Data model (SQLite)
devices · sessions · runs (JSON) · plans (JSON) · notes · offline_queue · credentials (DPAPI blobs) · audit (hash chain).

## Extension points
Add a diagnostic: `diag()` in tools.py + analyzer in diagnostics.py + mock fixture. Add a repair: `repair()` with a verify module/check + mock `_apply`. The test `test_risk_classification` guards that every repair declares verification.

## M2 additions
```
Technician laptop (control center)                                   Client machine (owner-controlled)
 Tauri UI ─▶ FastAPI ─▶ Core ─▶ Targets ──── TLS, pinned fingerprint ───▶ cit_agent (grants chosen by the owner)
                │         ├ Tasks (plan/approve/execute/verify/report)         ├ FsGuard (project roots only)
                │         ├ Dev (projects, git, build, backup)                  ├ JobManager (argv, timeout, cancel)
                │         ├ servers.py (allowlisted Linux actions, SSH)         └ claude job (client's own login)
                └ Central Claude: `claude -p` on THIS laptop against a local mirror of the project
```
- **Mode A (Central)**: pull project → local mirror (`workspaces/<device>/<project>/{base,work}`) → Claude Code edits only `work` → diff review → approved push (originals backed up, read-back verified) → tests on the target.
- **Mode B (Remote)**: project backup → agent starts `claude` in the project dir with the **client's** environment → output streamed to the dashboard → tests on the target. The controller has no code path that sends credentials (agent rejects unknown fields; test asserts it).
- `Target` protocol (`targets.py`): `LocalTarget`, `AgentClient`, `LinuxSshTarget`. All dev/server/task features use it, so they work identically on every target.
- Authorization on agent devices: consent happens on the client's screen (code + fingerprint); the controller records it as an active session limited by the agent's own expiry.

## M3: Worker Station
```
Laptop (control center) ── pinned TLS over Tailscale/LAN ──▶ cit_worker (service on the Worker PC)
  Workers manager (enroll, health monitor, proxy allowlist)     ├ Store (SQLite): credentials, clients, projects, tasks, events, checkpoints, audit chain
                                                                 ├ Scheduler: approved tasks → one per project, survives laptop disconnects
                                                                 ├ Per client: workspace dir · FsGuard · JobManager · secrets (DPAPI) · CLAUDE_CONFIG_DIR
                                                                 └ Locks: STOP file · authorization window · emergency stop · revoke-all · wipe
```
Recovery rule: on start, `running` → `interrupted`; a destructive task whose attempt marker was persisted → `needs_review`; orphan job processes (pid + start time match) are killed; nothing is re-run unless `auto_resume` and the task is a non-destructive Claude task with a saved session.
