# User guide (MVP)

## Run the app in development (mock devices)
```
cd backend && pip install -r requirements-dev.txt && CIT_API_TOKEN=devtoken python -m app.main
cd frontend && npm install && VITE_API_TOKEN=devtoken npm run dev     # http://localhost:1420
```
Windows installer: produced by the `windows-installer` CI job (unsigned, untested).

## Workflow
1. **Discovery & pairing** — register the device and the owner's authorization, then pair with the one-time code. Mock devices work immediately.
2. **Support sessions** — request a `diagnose` or `repair` session; the client confirms with the consent code. The green indicator shows an active session; Revoke ends it. Sessions expire automatically.
3. **Diagnostics** — run all 13 modules; findings are ranked by severity (rule-based, not AI).
4. **Repair plans** — create a plan, approve low-risk steps in bulk, tick the confirmation box for each high-risk step, execute. Each step shows ✔ verified / ✖ NOT verified. The after-repair re-scan is shown beneath.
5. **Reports** — download the PDF; add technician notes.

## Real PCs (not yet exercised)
Direct Ethernet: set static IPs (e.g. 169.254.10.1 and .2 / 255.255.0.0). A cable alone gives no remote access: install/enable *OpenSSH Server* on the target, add your public key, note the host key fingerprint, then register transport `OpenSSH` and verify the fingerprint with the owner. Remote: join both PCs to Tailscale and use the 100.x address. Never expose port 22/5986 to the Internet.

## Offline
If the Internet is down the header shows "Offline"; diagnostics and reports still work; chat replies are tagged *Scripted*.

## M2: working with a client (agent flow)
1. Copy the `cit-agent-portable` folder (CI artifact) to a USB drive. At the client: they double-click `start-agent.bat`, choose the project folder and what to allow, and read you the **8-digit code and the fingerprint**.
2. *Quick Connect* → pick “Portable USB Technician Agent” → enter their address (Ethernet: set 169.254.10.x on both; LAN; or Tailscale 100.x) → *Detect methods* → register with the owner's authorization → type code + fingerprint → *Pair & test*.
3. *Development Projects* → add the project folder (as it exists on the client) → explore/edit/test, or *AI Workspace* → Development → choose Central or Remote Claude, write the instruction, review the plan, approve, watch the live log, review the diff (Central), check verification, download the report.
4. Done? Click *Revoke* (or the client types `d`). *EMERGENCY STOP* ends everything at once.
Remote Claude needs the client to sign in to Claude Code with **their own** account (or API key). Never give them yours.
