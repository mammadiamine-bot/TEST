#!/usr/bin/env python3
"""Real end-to-end LAN verification. Runs on the MAIN PC against the controller backend and ONE agent address you give it.
Every line is a real request with real evidence. Nothing is simulated: if something cannot be verified it says BLOCKED or SKIPPED.

  python tools/lan_check.py --controller http://127.0.0.1:8765 --token <API token> --agent 192.168.1.23:8766 \\
      --code 12345678 --fingerprint "AAAAAAAA BBBBBBBB ..." --root "C:\\CITWork" [--with-claude] [--reconnect-test]
"""
import argparse, hashlib, json, os, sys, time, urllib.error, urllib.request

R = []


def rec(name, status, evidence=""):
    R.append((name, status, evidence)); print(f"[{status:7}] {name}" + (f" — {evidence}" if evidence else ""), flush=True)
    return status == "PASS"


class Api:
    def __init__(self, base, token): self.base, self.token = base.rstrip("/"), token

    def __call__(self, method, path, body=None, timeout=60):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try: d = json.loads(e.read())
            except Exception: d = {}
            return e.code, d


def ask(q, non_interactive):
    if non_interactive: return False
    return input(f"\n>>> {q} [y/N] ").strip().lower() == "y"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--controller", default="http://127.0.0.1:8765"); p.add_argument("--token", default=os.environ.get("CIT_API_TOKEN", ""))
    p.add_argument("--agent", required=True, help="host:port shown on the second PC's screen"); p.add_argument("--code", required=True); p.add_argument("--fingerprint", required=True)
    p.add_argument("--root", required=True, help="project folder on the SECOND PC that you authorized when starting the agent")
    p.add_argument("--name", default="Second PC"); p.add_argument("--with-claude", action="store_true"); p.add_argument("--reconnect-test", action="store_true")
    p.add_argument("--non-interactive", action="store_true", help="never prompt; steps that need a human are SKIPPED")
    a = p.parse_args()
    api = Api(a.controller, a.token)
    sep = "\\" if ("\\" in a.root or a.root[1:2] == ":") else "/"
    join = lambda *x: sep.join([a.root.rstrip("/\\"), *x])

    st, _ = api("GET", "/status")
    if not rec("controller reachable", "PASS" if st == 200 else "FAIL", f"HTTP {st} at {a.controller}"):
        return summary()
    host = a.agent.rsplit(":", 1)[0]
    st, pr = api("POST", "/connections/probe", {"address": a.agent})
    ports = [o["method"] for o in pr.get("open", [])] if st == 200 else []
    rec("network: agent port answers (single address probe, no scan)", "PASS" if "agent" in ports else "FAIL",
        f"open: {ports}" if st == 200 else str(pr.get("detail")) + " | hint: same Wi-Fi? router 'AP/client isolation' off? Windows Firewall allowed the agent on a PRIVATE network?")
    if "agent" not in ports: return summary()

    st, dev = api("POST", "/devices", {"name": a.name, "address": a.agent, "transport": "agent", "owner_name": "Technician (own computer)", "owner_contact": "own computer",
                                       "authorization_note": "Owner's own second computer, LAN test"})
    if not rec("register device", "PASS" if st == 201 else "FAIL", dev.get("id") or str(dev.get("detail"))): return summary()
    did = dev["id"]
    st, d = api("POST", f"/devices/{did}/pair", {"code": a.code, "fingerprint": a.fingerprint})
    if not rec("pair (code from the PC's screen + pinned TLS fingerprint)", "PASS" if st == 200 else "FAIL", f"status={d.get('status')}" if st == 200 else str(d.get("detail"))): return summary()
    st, ct = api("GET", f"/devices/{did}/connection-test")
    rec("connection test (network → identity → auth → permissions → tools)", "PASS" if ct.get("ok") else "FAIL", "; ".join(f"{c['stage']}={'ok' if c['ok'] else 'FAIL ' + str(c['detail'])}" for c in ct.get("checks", [])))
    st, inf = api("GET", f"/devices/{did}/info")
    info = inf.get("info", {})
    rec("device facts read from the machine itself", "PASS" if info.get("host") else "FAIL", f"host={info.get('host')} os={info.get('os')} allowed={info.get('grants')} tools=" + ",".join(k for k, v in (info.get("tools") or {}).items() if v.get("found")))
    grants, tools = info.get("grants") or [], {k for k, v in (info.get("tools") or {}).items() if v.get("found")}

    st, prj = api("POST", "/projects", {"device_id": did, "name": "lan-test", "root": a.root, "stack": "other"})
    if not rec("project folder accessible (inside what the owner authorized)", "PASS" if st == 201 else "FAIL", str(prj.get("detail") or prj.get("id"))): return summary()
    pid = prj["id"]

    def term(argv, timeout=60):
        return api("POST", f"/projects/{pid}/terminal", {"argv": argv, "timeout": timeout}, timeout=timeout + 20)
    if "terminal" in grants:
        st, r = term(["hostname"])
        rec("remote command execution (hostname)", "PASS" if st == 200 and r.get("exit") == 0 and r["output"].strip() else "FAIL", (r.get("output") or str(r.get("detail"))).strip()[:80])
        st, r = term(["whoami"]); rec("remote command execution (whoami)", "PASS" if st == 200 and r.get("exit") == 0 else "FAIL", (r.get("output") or "").strip()[:80])
    else:
        rec("remote command execution", "BLOCKED", "the owner did not allow commands: restart the agent answering Y to 'run commands'")

    if "write" in grants:
        payload = os.urandom(200_000); want = hashlib.sha256(payload).hexdigest()
        import base64
        st, _ = api("PUT", f"/devices/{did}/fs/write", {"path": join("transfer.bin"), "content_b64": base64.b64encode(payload).decode()})
        st2, rd = api("GET", f"/devices/{did}/fs/read?path=" + urllib.request.quote(join("transfer.bin")))
        got = hashlib.sha256(base64.b64decode(rd["content_b64"])).hexdigest() if st2 == 200 else ""
        rec("file transfer laptop → PC → laptop (200 KB, SHA-256 compared)", "PASS" if st == 200 and got == want else "FAIL", f"sha256 {want[:16]}… == {got[:16]}…")
    else:
        rec("file transfer", "BLOCKED", "the owner did not allow writing files")

    if "write" in grants and "terminal" in grants:
        code = "print('answer =', 6 * 7)\n"
        st, _ = api("PUT", f"/projects/{pid}/file", {"path": "hello.py", "content": code})
        py = "python" if "python" in tools else None
        if py:
            st, r = term([py, "hello.py"])
            rec("simple coding task: write hello.py remotely and run it", "PASS" if r.get("exit") == 0 and "answer = 42" in r.get("output", "") else "FAIL", (r.get("output") or "").strip()[:60])
        else:
            rec("simple coding task: run hello.py", "BLOCKED", "Python is not installed on the second PC (install it, or run the 'Install tools' task after approving)")
    else:
        rec("simple coding task", "BLOCKED", "needs the 'run commands' and 'write files' permissions")

    if a.with_claude:
        if "claude" in grants and "claude" in tools:
            st, t = api("POST", "/tasks", {"device_id": did, "kind": "dev", "project_id": pid, "mode": "remote", "instruction": "Create a file named claude_hello.txt containing the single line: hello from remote Claude. Do nothing else."})
            if st != 201 or t.get("status") == "blocked": rec("remote Claude task prepared", "BLOCKED" if st == 201 else "FAIL", t.get("mode_reason") or str(t.get("detail")))
            elif ask("Approve running Claude Code ON THE SECOND PC (it will edit files in the authorized folder; a backup is taken first)?", a.non_interactive):
                tid = t["id"]
                for s in t["steps"]:
                    if s["status"] == "proposed":
                        api("POST", f"/tasks/{tid}/steps/{s['id']}/approve", {"approver": "Technician", "confirm_high_risk": True})
                api("POST", f"/tasks/{tid}/execute"); seen = 0; end = time.time() + 600; d = {}
                while time.time() < end:
                    _, d = api("GET", f"/tasks/{tid}")
                    for e in d.get("events", [])[seen:]: print("     log|", e["msg"][:160], flush=True)
                    seen = len(d.get("events", []))
                    if d.get("status") not in ("running", "approved", "planned"): break
                    time.sleep(1.5)
                rec("remote Claude session: live log streamed + finished", "PASS" if d.get("status", "").startswith("completed") else "FAIL", f"status={d.get('status')}, {seen} log lines")
                st, rf = api("GET", f"/devices/{did}/fs/read?path=" + urllib.request.quote(join("claude_hello.txt")))
                rec("Claude's result exists on the second PC", "PASS" if st == 200 and b"hello" in base64.b64decode(rf["content_b64"]) else "FAIL", "claude_hello.txt")
            else: rec("remote Claude task", "SKIPPED", "not approved")
        else:
            rec("remote Claude session", "BLOCKED", f"agent grants={grants}, claude installed={'claude' in tools}. Needs: agent started with --allow-claude, Claude Code installed, and the CLIENT-side login done on that PC with its own account/key")

    if a.reconnect_test:
        if ask("Reconnect test: I will watch the connection. Turn OFF Wi-Fi on the SECOND PC for ~10 s, then ON again. Ready?", a.non_interactive):
            down = up = False; end = time.time() + 180
            while time.time() < end and not (down and up):
                st, ct = api("GET", f"/devices/{did}/connection-test", timeout=15)
                if not ct.get("ok"): down = True; print("     (connection lost — now turn Wi-Fi back on)", flush=True)
                elif down: up = True
                time.sleep(1.5)
            rec("reconnect after a real network interruption", "PASS" if down and up else "FAIL", f"lost={down} recovered={up} (the agent session stayed valid)")
        else: rec("reconnect after a real network interruption", "SKIPPED", "not run")

    st, ses = api("GET", f"/devices/{did}/sessions")
    act = next((s for s in ses if s["status"] == "active"), None)
    if act:
        api("POST", f"/sessions/{act['id']}/revoke")
        st, ct = api("GET", f"/devices/{did}/connection-test")
        rec("disconnect: session revoked, the PC no longer accepts commands", "PASS" if not ct.get("ok") else "FAIL", "agent ended the session and stopped")
    return summary()


def summary():
    print("\n==== SUMMARY ====")
    for n, s, e in R: print(f"{s:8} {n}")
    bad = [r for r in R if r[1] == "FAIL"]
    print(f"\n{sum(r[1]=='PASS' for r in R)} passed, {len(bad)} failed, {sum(r[1]=='BLOCKED' for r in R)} blocked, {sum(r[1]=='SKIPPED' for r in R)} skipped")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
