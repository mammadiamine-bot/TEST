#!/usr/bin/env python3
"""Lightweight lifecycle hooks for intelligent-development-orchestrator.
Usage: orchestrator.py <SessionStart|UserPromptSubmit|PostToolUse|Stop>
Never runs network calls, deploys, or full test suites. Always fails open."""
import json, os, re, subprocess, sys
from pathlib import Path

STATE = Path.home() / ".claude" / "state" / "orchestrator"
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".sh", ".css", ".html", ".sql", ".go", ".rs", ".java"}
VERIFY_RE = re.compile(r"\b(test|pytest|jest|vitest|playwright|build|lint|tsc|typecheck|mypy|ruff|eslint|cargo check|go vet|py_compile|node --check|bash -n)\b")
PROJECTS = {  # keyword in repo path/name -> isolated project label
    "maydan": "MAYDAN", "growth": "Growth OS", "swana": "SWANA", "media-hub": "AI Media Hub",
    "mediahub": "AI Media Hub", "page2store": "Page2Store AI", "property": "Property Manager",
    "barmajli": "Barmajli", "interactive-lab": "Barmajli Interactive Lab",
}
ROUTES = [  # (regex, hint) first-match-wins per category, max 2 hints
    (r"\b(ui|ux|design|layout|css|landing|responsive|banner|brand|figma|تصميم|واجهة)\b",
     "UI/UX: read DESIGN.md/CLAUDE.md design rules, use project design skills (ui-ux-pro-max/design-system), verify in browser (Playwright) at mobile+desktop, check a11y."),
    (r"\b(react|component|frontend|next\.?js|tailwind|vite|page)\b",
     "Frontend: inspect existing components first, reuse them, verify in browser, check perf/a11y."),
    (r"\b(database|sql|schema|migration|supabase|backend|api|endpoint|rls)\b",
     "Backend/DB: inspect schema (read-only) first, review auth/RLS/data isolation, add regression test; no prod writes without approval."),
    (r"\b(bug|error|fail|broken|crash|fix|debug|خطأ|مشكلة)\b",
     "Debug: reproduce -> root cause -> smallest fix -> verify the affected workflow."),
    (r"\b(security|auth|token|secret|permission|vulnerab|أمان)\b",
     "Security: review authn/authz, API access, secrets handling, data isolation."),
    (r"\b(deploy|release|publish|production|vercel|ship|نشر)\b",
     "Deploy: run build/release checks only; ASK before publishing or touching production."),
]

def out(event, ctx):
    if ctx:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}))

def root(cwd):
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3).stdout.strip() or cwd
    except Exception:
        return cwd

def sfile(sid, name):
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE / f"{re.sub(r'[^A-Za-z0-9_-]', '_', sid or 'nosession')}.{name}"

def main():
    ev = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        d = json.load(sys.stdin)
    except Exception:
        d = {}
    cwd, sid = d.get("cwd") or os.getcwd(), d.get("session_id", "")
    if ev == "SessionStart":
        r = Path(root(cwd))
        label = next((v for k, v in PROJECTS.items() if k in str(r).lower()), r.name)
        have = [n for n in ("CLAUDE.md", "DESIGN.md", ".mcp.json", ".claude/skills", ".claude/rules") if (r / n).exists()]
        pm = next((n for n in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "pyproject.toml", "requirements.txt") if (r / n).exists()), "none")
        out(ev, f"[orchestrator] Project: {label} ({r}). Present: {', '.join(have) or 'no project config'}; deps: {pm}. "
                f"Stay inside this project: do not mix code/credentials/DB config with other projects. Read its CLAUDE.md/DESIGN.md before editing.")
    elif ev == "UserPromptSubmit":
        p = (d.get("prompt") or "").lower()
        if len(p) < 25:
            return
        hints = [h for rx, h in ROUTES if re.search(rx, p)][:2]
        out(ev, "[orchestrator] " + " | ".join(hints) + " Use only the smallest set of relevant skills/tools." if hints else "")
    elif ev == "PostToolUse":
        tool, ti = d.get("tool_name", ""), d.get("tool_input") or {}
        if tool == "Bash":
            if VERIFY_RE.search(ti.get("command", "")):
                sfile(sid, "verified").write_text("1")
            return
        fp = ti.get("file_path") or ""
        ext = Path(fp).suffix.lower()
        if not fp or ext not in CODE_EXT:
            return
        with sfile(sid, "edits").open("a") as f:
            f.write(fp + "\n")
        try: sfile(sid, "verified").unlink()
        except FileNotFoundError: pass
        cmd = {".py": ["python3", "-m", "py_compile", fp], ".js": ["node", "--check", fp], ".mjs": ["node", "--check", fp],
               ".cjs": ["node", "--check", fp], ".sh": ["bash", "-n", fp]}.get(ext)
        err = ""
        if ext == ".json":
            try: json.loads(Path(fp).read_text())
            except Exception as e: err = str(e)
        elif cmd and Path(fp).exists():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            err = (r.stderr or r.stdout).strip() if r.returncode else ""
        if err:
            out(ev, f"[orchestrator] Syntax check FAILED for {fp}: {err[:400]}")
    elif ev == "Stop":
        if d.get("stop_hook_active") or sfile(sid, "blocked").exists():
            return
        edits, verified = sfile(sid, "edits"), sfile(sid, "verified")
        if edits.exists() and not verified.exists():
            sfile(sid, "blocked").write_text("1")  # block at most once per session -> no loops
            print(json.dumps({"decision": "block", "reason": "Code was edited but no verification (test/build/lint/browser check) ran since. Run a targeted check, or state clearly that the change is unverified. Do not claim completion without evidence."}))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
