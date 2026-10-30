# First real LAN test — main PC + second PC on the same Wi-Fi

**Status: NOT YET PERFORMED.** The assistant that wrote this runs in a cloud container with no access to your PCs, USB drive or network
(no computer was connected to the session), so none of the steps below has been done or simulated. Every step here is something you run;
`tools/lan_check.py` then proves each capability with real requests and prints PASS / FAIL / BLOCKED / SKIPPED.
Nothing here scans the network: you read the second PC's address from its own screen.

## 0. Get the programs (main PC)
GitHub → this repo → *Actions* → latest green run of the branch → download artifacts **`cit-agent-portable`**, **`cit-worker`**, and (optional) **`installer`**.
For the very first test run the dashboard from source (the installer was built in CI but never installed by anyone):
```
cd backend  && pip install -r requirements.txt && set CIT_API_TOKEN=pick-any-long-secret && python -m app.main
cd frontend && npm install && set VITE_API_TOKEN=pick-any-long-secret && npm run dev      # then open http://localhost:1420
```
## 1–3. USB drive + this PC's network (read-only until you type YES)
```
powershell -ExecutionPolicy Bypass -File tools\prepare-usb.ps1 -Source "C:\Users\you\Downloads\cit-agent-portable.zip"
```
It prints this PC's Wi-Fi IPv4/gateway/network category, lists only *removable* drives, asks which one, refuses if `<drive>:\CITAgent` already exists,
copies (never formats/deletes/overwrites) and verifies SHA-256. A static test forbids destructive commands in the script. **Untested on Windows — read it first (it is short).**
Both PCs' Wi-Fi must be category **Private** (Settings → Network → Properties). If the router has "AP / client isolation", PCs cannot talk to each other: turn it off or use the phone hotspot.

## 4–5. Second PC: start the agent (physical steps for you)
1. Plug the USB drive into the second PC → open `CITAgent` → double-click **start-agent.bat**.
2. Answer the prompts. For this test choose a folder such as `C:\CITWork` (create it first), **Y** to run commands, **Y** to write files; **Y** to Claude only if you want the Claude step.
3. Windows Defender Firewall will ask to allow `cit-agent.exe`: **you** decide — tick **Private networks only**. (This is the only firewall change; the app never changes firewall settings itself.)
4. The window shows **Computer, Addresses (e.g. 192.168.1.23), Pairing code, Fingerprint**. Keep it open. `d` + Enter disconnects instantly.
If the agent cannot work (security software blocks it), fall back to OpenSSH Server on the second PC — that needs Administrator and firewall changes, so ask first; the SSH transport to Windows is untested.

## 6–9. Verify everything
```
python tools\lan_check.py --controller http://127.0.0.1:8765 --token <your CIT_API_TOKEN> ^
   --agent 192.168.1.23:8766 --code <8 digits> --fingerprint "<fingerprint from the screen>" --root "C:\CITWork" --reconnect-test --with-claude
```
It checks, with real traffic: port answers → pairing with pinned fingerprint → connection test → facts from the PC → **remote `hostname`/`whoami`** → **200 KB file round trip with SHA-256**
→ **simple coding task** (writes `hello.py`, runs it; BLOCKED if Python is missing) → optional **remote Claude** (asks you before approving; streams the live log) →
**reconnect** (you switch the second PC's Wi-Fi off ~10 s and on) → **disconnect** (session revoked, agent stops). The same screens are in the dashboard:
*Quick Connect*, *Devices → Details / test*, *AI Workspace*, *Remote Claude Sessions*.

## Claude Code on the second PC (needs your approval for each install)
Not installed by anything automatic. In the dashboard: AI Workspace → *Install dev tools* → tick node/git/python/claude → review the plan → approve each high-risk step (needs `winget`; requires the agent's *run commands* permission).
Then **on the second PC itself** sign Claude Code in with a **separate account or API key** — never your personal one. For a Worker the key is stored per client (*AI Worker Stations → Clients & projects → ANTHROPIC_API_KEY*).

## 10. Worker Station on the second PC (after the agent test passes)
On the second PC: `cit-worker.exe authorize --days 3` · `cit-worker.exe run --bind <its LAN IP> --port 8777` (allow Private networks in the firewall prompt) · `cit-worker.exe enroll` → in the dashboard *AI Worker Stations → Add worker* (address, fingerprint, code).
Tailscale/Ethernet are not needed for this LAN test. Optional later: `cit-worker.exe firewall-script` (you review, you run).

## What a green run proves — and what it does not
Proves: pairing, identity pinning, command execution, file transfer, a coding task, (optionally) a real remote Claude session, reconnect and disconnect **on your Wi-Fi with your two PCs**.
Does not prove: Tailscale, Ethernet, DPAPI-protected worker secrets (see CI `worker-windows`), the installer, or production readiness.
