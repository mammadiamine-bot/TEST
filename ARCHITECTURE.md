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
