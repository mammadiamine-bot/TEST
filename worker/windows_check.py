"""Runs ON WINDOWS in CI against the frozen cit-worker.exe: proves DPAPI protect/unprotect, enrollment and restart-persistence
with the real OS-backed secret store (no --dev-insecure-store). Exit code 0 = all passed."""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

exe = sys.argv[1]
d = Path(tempfile.mkdtemp(prefix="citw-"))
base = [exe, "--data-dir", str(d)]
def run(*a): return subprocess.run([*base, *a], capture_output=True, text=True, timeout=60)
def stop(p):
    """A PyInstaller one-file exe is bootloader + child; Popen.kill() leaves the child holding the port and the SQLite file."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    p.wait()
    time.sleep(1)


def start(port):
    p = subprocess.Popen([*base, "run", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out, fp = "", None
    for _ in range(200):
        line = p.stdout.readline(); out += line
        if "Fingerprint:" in line: fp = "".join(line.split(":")[1].split()).lower()
        if "Secret store:" in line and fp: return p, fp, out  # printed right after the fingerprint
    p.kill()
    raise SystemExit("worker did not start:\n" + out)

code =re.search(r"(\d{8})", run("enroll").stdout).group(1)
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
stop(p)
p2, fp2, _ = start(18777)
assert fp2 == fp, "identity changed after restart"
assert c.call("GET", "/status")["worker_id"]
stop(p2)
out = run("wipe", "--name", json.loads(run("status").stdout)["name"])
assert not d.exists() or not any(d.iterdir()), out.stdout + out.stderr
print("windows worker checks OK")
