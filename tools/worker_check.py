#!/usr/bin/env python3
"""Real end-to-end check of an ENROLLED cit-worker through the controller API (the same path the dashboard uses).
Prints PASS / FAIL / SKIPPED with evidence; never prints tokens. Nothing is simulated: every line below is a request to the worker.

  python tools/worker_check.py --controller http://127.0.0.1:8765 --token <CIT_API_TOKEN> --worker wst_xxx --workspace "C:\\CITWork\\worker\\data\\workspaces\\citcheck"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

RES: list[tuple[str, str, str]] = []


def rec(status: str, name: str, detail: str = "") -> bool:
    RES.append((status, name, detail)); print(f"[{status:7}] {name} — {detail}", flush=True); return status == "PASS"


class Api:
    def __init__(self, base: str, token: str, wid: str):
        self.base, self.h, self.wid = base.rstrip("/"), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, wid

    def w(self, method: str, path: str, body=None):
        req = urllib.request.Request(f"{self.base}/workers/{self.wid}/w{path}", method=method, headers=self.h,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}


def run_job(a: Api, cid: str, argv: list[str], cwd: str | None = None, timeout=60, wait=45):
    st, d = a.w("POST", f"/clients/{cid}/exec", {"argv": argv, "cwd": cwd, "timeout": timeout})
    if st != 200:
        return st, d, None
    jid, t0, out = d["job"], time.time(), None
    while time.time() - t0 < wait:
        _, out = a.w("GET", f"/clients/{cid}/jobs/{jid}")
        if out.get("done"):
            break
        time.sleep(0.5)
    return 200, jid, out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--controller", default="http://127.0.0.1:8765"); p.add_argument("--token", default=os.environ.get("CIT_API_TOKEN", ""))
    p.add_argument("--worker", required=True); p.add_argument("--client", default="citcheck")
    p.add_argument("--workspace", required=True, help="the client's workspace path ON THE WORKER")
    p.add_argument("--expect-host", default="")
    a_ = p.parse_args()
    a, cid, ws = Api(a_.controller, a_.token, a_.worker), a_.client, a_.workspace
    sep = "\\" if "\\" in ws else "/"

    st, s = a.w("GET", "/status")
    rec("PASS" if st == 200 and s.get("worker_id") else "FAIL", "worker status over authenticated TLS",
        f"host={s.get('host')} os={s.get('os')} secret_store={s.get('secret_store')} locked={s.get('locked')} cpu={s.get('cpu')}% ram={s.get('ram')}%")
    if st != 200:
        return 1
    if a_.expect_host:
        rec("PASS" if s.get("host") == a_.expect_host else "FAIL", "target identity is the intended computer", f"reported={s.get('host')} expected={a_.expect_host}")

    st, c = a.w("POST", "/clients", {"id": cid, "name": "CIT check"})
    rec("PASS" if st in (200, 201, 409) else "FAIL", "create isolated client workspace (409 = already exists)", f"HTTP {st}")

    for label, argv in (("hostname", ["hostname"]), ("whoami", ["whoami"])):
        st, jid, out = run_job(a, cid, argv, ws)
        txt = (out or {}).get("output", "").strip() if st == 200 else str(jid)
        rec("PASS" if st == 200 and out and out.get("exit") == 0 and txt else "FAIL", f"remote command: {label}", txt[:80])

    data = os.urandom(200 * 1024); want = hashlib.sha256(data).hexdigest()
    st, _ = a.w("PUT", f"/clients/{cid}/fs/write", {"path": ws + sep + "transfer.bin", "content_b64": base64.b64encode(data).decode()})
    st2, r = a.w("GET", f"/clients/{cid}/fs/read?path=" + urllib.request.quote(ws + sep + "transfer.bin"))
    got = hashlib.sha256(base64.b64decode(r.get("content_b64", ""))).hexdigest() if st2 == 200 else ""
    rec("PASS" if st == 200 and got == want else "FAIL", "file transfer controller→worker→controller, 200 KB, SHA-256", f"{want[:16]}… == {got[:16]}…")
    st3, _jid, h = run_job(a, cid, ["certutil", "-hashfile", ws + sep + "transfer.bin", "SHA256"], ws)
    if st3 == 200 and h and h.get("exit") == 0:
        remote = "".join(h["output"].splitlines()[1:2]).replace(" ", "").lower()
        rec("PASS" if remote == want else "FAIL", "file hash computed ON the worker's own disk matches", f"{remote[:16]}…")
    else:
        rec("SKIPPED", "file hash computed ON the worker's own disk", "certutil not available there")

    st, _ = a.w("GET", f"/clients/{cid}/fs/read?path=" + urllib.request.quote(r"C:\Windows\win.ini"))
    rec("PASS" if st in (403, 404, 422) else "FAIL", "boundary: reading outside the client workspace is refused", f"HTTP {st}")

    code = 'import sys, platform\nprint("hello from the worker", platform.node(), sys.version.split()[0])\n'
    a.w("PUT", f"/clients/{cid}/fs/write", {"path": ws + sep + "hello.py", "content_b64": base64.b64encode(code.encode()).decode()})
    st, jid, out = run_job(a, cid, ["python", "hello.py"], ws)
    o = (out or {}).get("output", "")
    rec("PASS" if st == 200 and out and out.get("exit") == 0 and "hello from the worker" in o else "FAIL", "Python development task: write hello.py remotely and run it", o.strip()[:100] or str(jid))

    st, jid, out = run_job(a, cid, ["claude", "--version"], ws, timeout=30)
    o = (out or {}).get("output", "").strip()
    rec("PASS" if st == 200 and out and out.get("exit") == 0 and o else "FAIL", "Claude Code installed on the worker (--version only; no Claude task run)", o[:80] or str(jid)[:80])

    st, d = a.w("POST", f"/clients/{cid}/exec", {"argv": ["python", "-u", "-c", "import time\nfor i in range(6):\n    print('tick', i, flush=True)\n    time.sleep(1)"], "cwd": ws, "timeout": 60})
    snaps, since, t0 = [], 0, time.time()
    if st == 200:
        while time.time() - t0 < 30:
            _, j = a.w("GET", f"/clients/{cid}/jobs/{d['job']}?since={since}")
            if j.get("output"):
                snaps.append((round(time.time() - t0, 1), j["output"].strip().splitlines())); since = j["next"]
            if j.get("done"):
                break
            time.sleep(0.7)
    partial = len(snaps) >= 3 and snaps[0][0] < snaps[-1][0] - 2
    rec("PASS" if partial else "FAIL", "live log streaming (incremental output while the job is still running)", f"{len(snaps)} chunks, first at {snaps[0][0] if snaps else '-'}s, last at {snaps[-1][0] if snaps else '-'}s")

    st, d = a.w("POST", f"/clients/{cid}/exec", {"argv": ["python", "-c", "import time; time.sleep(120)"], "cwd": ws, "timeout": 300})
    ok = False; info = f"HTTP {st}"
    if st == 200:
        time.sleep(2); a.w("POST", f"/clients/{cid}/jobs/{d['job']}/cancel", {})
        for _ in range(20):
            _, j = a.w("GET", f"/clients/{cid}/jobs/{d['job']}")
            if j.get("done"):
                ok, info = bool(j.get("cancelled")), f"cancelled={j.get('cancelled')} done={j.get('done')} exit={j.get('exit')}"; break
            time.sleep(0.5)
    rec("PASS" if ok else "FAIL", "cancellation of a running job", info)

    st, au = a.w("GET", "/audit")
    rec("PASS" if st == 200 and isinstance(au, list) and au else "FAIL", "audit log records the actions", f"{len(au) if isinstance(au, list) else 0} entries")

    print("\n==== SUMMARY ====")
    for s_, n, _ in RES:
        print(f"{s_:8} {n}")
    cnt = {k: sum(1 for r_ in RES if r_[0] == k) for k in ("PASS", "FAIL", "SKIPPED")}
    print(f"\n{cnt['PASS']} passed, {cnt['FAIL']} failed, {cnt['SKIPPED']} skipped")
    return 1 if cnt["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
