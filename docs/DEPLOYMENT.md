# Deployment and operations

Status: the controller runs on Windows today (verified on the real Windows 11 PC). The Docker/Caddy stack below is **written but NOT VERIFIED** (no Docker in the authoring environment); CI validates it on its first run.

## 1. Windows development setup (verified)
```
cd backend
py -3.12 -m venv ..\work\venv
..\work\venv\Scripts\pip install -r requirements.txt
set CIT_API_TOKEN=<long random>
..\work\venv\Scripts\python -m app.main            (listens on 127.0.0.1:8765)
cd ..\frontend && npm ci && set VITE_API_TOKEN=<same token> && npm run dev      (http://127.0.0.1:1420)
```
Tests: `cd backend && python -m pytest -q`. On Windows about 26 older tests assume Linux tools and fail (known, none in RMM code); the RMM suites `test_tenancy test_rbac test_monitor test_ops test_billing test_ai test_ops_admin` pass.

## 2. Server deployment (Docker Compose, unverified)
1. Install Docker and point a DNS name at the server. Open only ports 80 and 443.
2. `cp deploy/.env.example deploy/.env`; set `CIT_DOMAIN`, a 48+ character `CIT_API_TOKEN` (or mount `CIT_API_TOKEN_FILE`), and optionally SMTP and Ollama.
3. `docker compose -f deploy/docker-compose.yml up -d --build`. Caddy obtains HTTPS automatically; the API is reachable only at `https://<domain>/api` through the proxy.
4. Sign in to the dashboard with the owner token, create clients, and invite administrators (each gets a personal token). Do not share the owner token.
5. Reaching Workers: the controller connects *out* to each Worker. Run Tailscale/WireGuard on the host (not tested yet) and give the backend host networking, or run the controller as a Windows service instead of Docker. Never publish Worker ports.

Development and production are separated: development uses `CIT_ALLOW_MOCK=1`, loopback binding and `VITE_API_TOKEN`; production sets `CIT_ENV=production`, `CIT_ALLOW_MOCK=0` and uses the web build, where each person signs in with their own token (kept per tab, never in the bundle).

## 3. Health, logs, monitoring
* `GET /health` - process is up. `GET /ready` - database answers and the audit chain verifies (503 otherwise). The Docker `HEALTHCHECK` uses `/ready`.
* Logs: one JSON object per line on stdout (`CIT_LOG_FORMAT=json`, `CIT_LOG_LEVEL`). Ship them with your log agent.
* Fleet monitoring and alerting are part of the product (Monitoring page). E-mail needs SMTP settings; without them alerts are recorded as `not_sent_no_smtp`.

## 4. Backup and restore (verified by tests)
```
python -m app.backup create  --data-dir <dir> [--out <dir>]    consistent online copy + SHA-256 + license.key copy
python -m app.backup verify  <file>                            integrity + audit-chain check
python -m app.backup restore <file> --data-dir <dir>           stop the controller first; the previous DB is kept as .before-restore-*
```
Run `create` nightly (Task Scheduler or cron) and copy the output off the machine. DPAPI-protected secrets open only for the same Windows user on the same machine; after restoring to new hardware the Workers must be re-enrolled.

## 5. Update and rollback
Before updating run `python -m app.backup create`. Pull the new version and restart; the first start migrates the database after writing `cit.sqlite3.pre-v<N>.bak`. To roll back: stop, restore the previous version and the backup. Worker updates are **manual** (copy the new `cit-worker.exe`, restart). Automatic signed updates are NOT IMPLEMENTED.

## 6. CI
`.github/workflows/ci.yml`: backend tests, agent/worker selftests, frontend type-check/test/build, Docker config and image build validation, and the Windows installer job (still unproven).
