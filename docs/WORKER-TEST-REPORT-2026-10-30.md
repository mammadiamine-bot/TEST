# cit-worker on the real Windows 10 PC — 2026-10-30 (manual mode, no startup task)

Controller: Windows 11 main PC (backend in a venv, local API token). Worker: Windows 10 PC `DESKTOP-1KV25VV`, same Wi-Fi (Private), `cit-worker.exe`
0.1.0 built locally from commit 293de93 (SHA-256 270f2d6c…52c97, verified after the USB copy), run in a visible console from `C:\CITWork\worker`.
Pairing: owner compared all 8 fingerprint groups on the Worker's screen with the certificate read from the live connection, then enrolled with the one-time code.
Run with `tools/worker_check.py` through the controller API (the path the dashboard uses). Nothing simulated.

| Result | Step | Evidence |
|---|---|---|
| PASS | enrollment with pinned fingerprint + one-time code | worker `wst_…` online, credential persisted on the controller (DPAPI) |
| PASS | status over authenticated TLS | host DESKTOP-1KV25VV, os windows, `secret_store=os-backed`, locked=None |
| PASS | target identity check before acting | reported host == expected host |
| PASS | isolated client workspace | `C:\CITWork\worker\data\workspaces\citcheck` |
| PASS | remote `hostname` / `whoami` | DESKTOP-1KV25VV / desktop-1kv25vv\yaman |
| PASS | file transfer 200 KB controller→worker→controller | SHA-256 equal on both ends |
| PASS | same file hashed by `certutil` on the worker's own disk | hash equal |
| PASS | boundary: read outside the workspace (`C:\Windows\win.ini`) | HTTP 403 |
| PASS | Python task: write hello.py remotely and run it | `hello from the worker DESKTOP-1KV25VV 3.12.10` |
| PASS | Claude Code installed (`claude --version` only; no Claude task run) | 2.1.314 |
| PASS | live log streaming | 5 incremental chunks over 5 s while the job was running |
| PASS | cancellation of a running job | cancelled=True, done=True |
| PASS | audit log | 18 entries |
| PASS | controller restart → reconnect without re-enrolling | after restart: state `online`, authenticated call returned the host |
| NOT RUN | reconnect after a real network interruption of the Worker (Wi-Fi off/on) | needs the owner at the Worker; see next steps |
| NOT RUN | Claude Code **task** on the worker | not approved / no separate login checked |
| NOT RUN | revoke / lock from the Worker | destructive to the connection; run last, with the owner |
| NOT DONE | automatic start at Windows logon | awaiting owner approval; no scheduled task exists |

13 + 1 passed, 0 failed. **24/7 operation is not claimed**: nothing has run unattended, nothing survived a Worker reboot or a Wi-Fi drop yet.
