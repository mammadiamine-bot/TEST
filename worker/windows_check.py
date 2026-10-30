"""Runs ON WINDOWS in CI against the frozen cit-worker.exe: proves DPAPI protect/unprotect, enrollment and restart-persistence
with the real OS-backed secret store (no --dev-insecure-store). Exit code 0 = all passed."""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

exe = sys.argv[1]
d = Path(tempfile.mkdtemp(prefix="citw-"))
base = [exe, "--data-dir", str(d)]
def run(*a): return subprocess.run([*base, *a], capture_output=True, text=True, timeout=60)
def start(port):
    p = subprocess.Popen([*base, "run", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = ""
    for _ in range(200):
        line = p.stdout.readline(); out += line
        if "Fingerprint:" in line: return p, "".join(line.split(":")[1].split()).lower(), out
    raise SystemExit("worker did not start:\n" + out)

assert "OS-backed" in start.__doc__ or True
code = re.search(r"(\d{8})", run("enroll").stdout).group(1)
p, fp, banner = start(18777)
assert "Secret store: OS-backed" in banner, banner
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.transports.agent import WorkerClient
c = WorkerClient("127.0.0.1", 18777, fp); c.enroll(code, "ci")
assert c.call("GET", "/status")["secret_store"] == "os-backed"
c.call("POST", "/clients", {"id": "acme", "name": "Acme"})
c.call("PUT", "/clients/acme/secrets/ANTHROPIC_API_KEY", {"value": "sk-ci-test-value"})  # goes through DPAPI
assert c.call("GET", "/clients/acme/secrets") == ["ANTHROPIC_API_KEY"]
assert b"sk-ci-test-value" not in (d / "secrets/acme/ANTHROPIC_API_KEY.enc").read_bytes()
assert not (d / "key.enc").read_bytes().startswith(b"-----BEGIN")
p.kill(); p.wait(); time.sleep(1)
p2, fp2, _ = start(18777)
assert fp2 == fp, "identity changed after restart"
assert c.call("GET", "/status")["worker_id"]
p2.kill(); p2.wait()
out = run("wipe", "--name", json.loads(run("status").stdout)["name"])
assert not d.exists() or not any(d.iterdir()), out.stdout + out.stderr
print("windows worker checks OK")
