import os
import sys
import time

import pytest

from app.targetcore import FsGuard, Grants, JobManager, TargetError, claude_argv, detect_tools, redact_argv, scrub_env
from app.targets import LocalTarget


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"; r.mkdir()
    (r / "a.txt").write_text("hello")
    (tmp_path / "secret.txt").write_text("nope")
    return r


def test_fs_confined_to_root(root, tmp_path):
    g = FsGuard([root])
    assert g.read("a.txt") == b"hello"
    for bad in ["../secret.txt", str(tmp_path / "secret.txt"), "/etc/passwd", "sub/../../secret.txt"]:
        with pytest.raises(TargetError) as e:
            g.read(bad)
        assert e.value.status in (403, 404)


def test_symlink_cannot_escape(root, tmp_path):
    os.symlink(tmp_path / "secret.txt", root / "link.txt")
    os.symlink(tmp_path, root / "dirlink")
    g = FsGuard([root])
    with pytest.raises(TargetError):
        g.read("link.txt")
    with pytest.raises(TargetError):
        g.list("dirlink")
    with pytest.raises(TargetError):
        g.write("dirlink/x.txt", b"x")
    assert not (tmp_path / "x.txt").exists()


def test_write_delete_rules(root):
    g = FsGuard([root])
    g.write("src/new.txt", b"x")
    assert (root / "src/new.txt").read_bytes() == b"x"
    with pytest.raises(TargetError):
        g.delete("src")  # directories are never deleted
    g.delete("src/new.txt")
    assert not (root / "src/new.txt").exists()
    with pytest.raises(TargetError):
        g.write("big", b"0" * (6 * 1024 * 1024))


def test_grants_default_deny(root):
    t = LocalTarget([root], Grants())
    for f in (lambda: t.fs_write("a.txt", b"x"), lambda: t.exec_start(["echo", "x"], None, 5),
              lambda: t.claude_start("hi", str(root), "default", [], 5)):
        with pytest.raises(TargetError) as e:
            f()
        assert e.value.status == 403
    assert t.fs_read("a.txt") == b"hello"


def _wait(t, jid, secs=10):
    end = time.time() + secs
    while time.time() < end:
        j = t.job(jid)
        if j["done"]:
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_exec_streams_output_and_exit_code(root):
    t = LocalTarget([root])
    j = _wait(t, t.exec_start([sys.executable, "-c", "print('hi'); import sys; sys.exit(3)"], None, 20))
    assert "hi" in j["output"] and j["exit"] == 3


def test_exec_is_not_shell_and_env_scrubbed(root, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "supersecret")
    t = LocalTarget([root])
    j = _wait(t, t.exec_start([sys.executable, "-c", "import os;print(os.environ.get('MY_API_TOKEN'),'x;y|z')"], None, 20))
    assert j["output"].strip() == "None x;y|z"
    with pytest.raises(TargetError):
        t.exec_start(["definitely-not-a-binary"], None, 5)
    with pytest.raises(TargetError):
        t.exec_start([sys.executable], str(root.parent), 5)  # cwd outside root


def test_cancel_and_timeout(root):
    t = LocalTarget([root])
    jid = t.exec_start([sys.executable, "-c", "import time;time.sleep(60)"], None, 60)
    time.sleep(0.3)
    t.job_cancel(jid)
    j = _wait(t, jid)
    assert j["cancelled"] and j["exit"] != 0
    jid = t.exec_start([sys.executable, "-c", "import time;time.sleep(60)"], None, 1)
    assert _wait(t, jid, 8)["timed_out"]


def test_incremental_output(root):
    t = LocalTarget([root])
    jid = t.exec_start([sys.executable, "-u", "-c", "import time;print('one',flush=True);time.sleep(0.8);print('two',flush=True)"], None, 20)
    time.sleep(0.4)
    a = t.job(jid)
    assert "one" in a["output"] and "two" not in a["output"]
    done = _wait(t, jid)
    assert "two" in t.job(jid, a["next"])["output"] and done["exit"] == 0


def test_claude_argv_never_bypasses_permissions():
    a = claude_argv("do it", "acceptEdits", ["Read", "Edit"])
    assert a[:3] == ["claude", "-p", "do it"] and "bypassPermissions" not in a
    for bad in ("bypassPermissions", "dontAsk", ""):
        with pytest.raises(TargetError):
            claude_argv("x", bad, [])
    with pytest.raises(TargetError):
        claude_argv("x", "default", ["Bash; rm -rf /"])


def test_redaction_and_env():
    r = redact_argv(["tool", "--password", "hunter2", "--token=abc", "api_key=zzz", "sk-ant-abcdefghijklmnop", "ok"])
    assert "hunter2" not in " ".join(r) and "abc" not in " ".join(r) and "zzz" not in " ".join(r) and "sk-ant" not in " ".join(r)
    assert r[-1] == "ok" and r[0] == "tool"
    assert "PATH" in scrub_env({"PATH": "/x", "GH_TOKEN": "t", "DB_PASSWORD": "p"}) and "GH_TOKEN" not in scrub_env({"GH_TOKEN": "t"})


def test_detect_tools_shape():
    d = detect_tools(["git", "node"])
    assert set(d) == {"git", "node"} and all("found" in v for v in d.values())


def _fake_run(rc, out="", err=""):
    import subprocess as sp
    return lambda *a, **k: sp.CompletedProcess(a[0], rc, out, err)


def test_detect_python_rejects_store_alias(monkeypatch):
    from app import targetcore as tc
    monkeypatch.setattr(tc.shutil, "which", lambda n, **k: r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe")
    monkeypatch.setattr(tc.subprocess, "run", _fake_run(9009, err="Python was not found; run without arguments to install from the Microsoft Store"))
    d = tc.detect_tools(["python"])["python"]
    assert d["found"] is False and d["version"] is None and "Store" in d["note"]
    monkeypatch.setattr(tc.subprocess, "run", _fake_run(0, out="Python 3.12.8"))
    d = tc.detect_tools(["python"])["python"]
    assert d["found"] is True and d["version"] == "Python 3.12.8"
