#!/usr/bin/env python3
"""TEST DOUBLE for the `claude` CLI. NOT Claude. Emits stream-json like `claude -p --output-format stream-json`
and applies a trivial scripted edit so the surrounding machinery (jobs, streaming, diff, push, verify) can be tested.
Emits a session_id in the init event; supports --resume.
Prompt directives: 'FAKE:replace A=>B in FILE' | 'FAKE:delete FILE' | 'FAKE:sleep' | 'FAKE:fail'."""
import json, os, re, sys, time
a = sys.argv[1:]
if "--version" in a:
    print("fake-claude 0.0 (test double)"); sys.exit(0)
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    open(log, "a").write(json.dumps({"argv": a, "cwd": os.getcwd(), "has_key": "ANTHROPIC_API_KEY" in os.environ, "key": os.environ.get("ANTHROPIC_API_KEY"), "config": os.environ.get("CLAUDE_CONFIG_DIR"), "leak": [k for k in os.environ if k.startswith("OWNER_")]}) + "\n")
prompt = a[a.index("-p") + 1]
def emit(o): print(json.dumps(o), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "sess-" + (a[a.index("--resume") + 1] if "--resume" in a else "0123456789abcdef")})
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking at the project"}]}})
m = re.search(r"FAKE:replace (.+?)=>(.+?) in (\S+)", prompt)
if m:
    f = m.group(3); s = open(f).read(); open(f, "w").write(s.replace(m.group(1), m.group(2)))
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}}]}})
m = re.search(r"FAKE:delete (\S+)", prompt)
if m:
    os.remove(m.group(1))
if "FAKE:sleep" in prompt:
    for i in range(300):
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": f"working {i}"}]}}); time.sleep(0.2)
if "FAKE:fail" in prompt:
    emit({"type": "result", "is_error": True, "result": "Invalid API key"}); sys.exit(1)
emit({"type": "result", "is_error": False, "result": "Done", "total_cost_usd": 0.0012})
