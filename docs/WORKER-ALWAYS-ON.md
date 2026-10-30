# Always-on Windows 10 AI Worker (cit-worker) — deployment runbook

Goal: the second PC runs `cit-worker.exe` from its own disk (no USB), starts after the owner logs in, survives network drops, and can be
revoked at any time. **Nothing here is applied automatically.** Every step that changes the Worker PC is yours to run or approve.

## Why the Worker and not the portable agent
`cit-agent` is a deliberately short, owner-present session (code + fingerprint every time, 240-minute cap). `cit-worker` is the persistent design:
stable TLS identity, one-time enrollment, a revocable controller credential, DPAPI-protected secrets, no expiry unless you set one,
automatic reconnect from the controller (GET retried with backoff; a different certificate on the same address is refused).
Unlimited time never means unlimited privileges: access stays per-client/per-project, Claude Code is never started unless you create a Claude task, and nothing is exposed beyond private/Tailscale source addresses.

## 1. Copy to the Worker's internal disk (no install, no admin)
On the Worker: create `C:\CITWorker`, copy `cit-worker.exe` from the USB folder `CITWorker`, compare the SHA-256 printed by `prepare` on the main PC
(`Get-FileHash C:\CITWorker\cit-worker.exe`). After this the USB is not needed.
Keep **all state in one folder you can delete**: `C:\CITWorker\data` (identity, DPAPI-protected key, database, per-client workspaces).
`C:\CITWork` (the old agent folder) is not touched. The Worker keeps each client's files in `C:\CITWorker\data\workspaces\<client>`.

## 2. First run (visible console) and pairing
```
cd C:\CITWorker
cit-worker.exe --data-dir C:\CITWorker\data run --bind 192.168.1.105 --port 8777
```
Windows Firewall asks once for `cit-worker.exe`: tick **Private networks only** (your decision; the worker never changes firewall rules). Never choose Public.
In a second window on the Worker: `cit-worker.exe --data-dir C:\CITWorker\data enroll` prints a one-time 8-digit code (10 minutes).
The banner shows the **Fingerprint**; on the main PC dashboard → *AI Worker Stations → Add worker* enter address `192.168.1.105:8777`, the code, and confirm the fingerprint matches the screen.
Check the banner says `Secret store: OS-backed` (DPAPI). If it says NOT OS-backed, stop.

## 3. Start automatically after login (needs your approval; reversible)
```
cit-worker.exe --data-dir C:\CITWorker\data service install --user <WindowsAccount> --i-understand
cit-worker.exe --data-dir C:\CITWorker\data service show
cit-worker.exe --data-dir C:\CITWorker\data service remove      :: undo
```
Creates exactly one scheduled task, "Claude IT Technician Worker", trigger *at log on of that user*, interactive-only (`/IT`), limited rights, no stored password.
It runs in that user's session, so DPAPI keeps working. Duplicate instances are refused (the port and the data directory are single-owner).

## 4. Control, health and revocation
| Need | How |
|---|---|
| Status | `cit-worker.exe --data-dir C:\CITWorker\data status`, dashboard *AI Worker Stations* (online / offline / stale + CPU, RAM, disk) |
| Stop everything now | create a file named `STOP` in `C:\CITWorker\data`, or `lock` |
| Revoke the main PC | `revoke-all` (the main PC must enroll again with a new code) |
| Expire access automatically | `authorize --days N` (default: none) |
| Remove everything | `wipe --name <worker name>`, then `service remove` |

## 5. What stays disabled until you approve it separately
Claude Code runs (no Claude task is created by any of the above) · Windows repair tools · firewall script (`firewall-script` only prints a script for you to review) · Tailscale/RustDesk · any public exposure.

## What a green run proves
See `docs/LAN-TEST-REPORT-*.md` for the dated evidence. Anything not listed there as PASS is not verified.
