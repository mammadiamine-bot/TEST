"""AI Technician. Claude only EXPLAINS evidence; it has no tool access and cannot run anything.
Repair plans are built from the allowlist by rules (service.create_plan). When cloud AI is
unavailable, answers come from a deterministic provider that is labelled "scripted"."""
from __future__ import annotations

import asyncio
import re

SYSTEM = ("You are an IT technician assistant. You are given redacted diagnostic findings from a Windows PC. "
          "Explain the problems in plain language, rank them, and suggest what to check next. You cannot run "
          "commands. Never claim anything was fixed. Reply in the user's language.")

_PATTERNS = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"(?i)(C:\\Users\\)[^\\\s]+"), r"\1<user>"),
]


def redact(text: str) -> str:
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


def findings_brief(findings: list[dict], evidence: bool = True) -> str:
    lines = [f"[{f['severity'].upper()}] {f['title']}" + (f" — {f['evidence']}" if evidence else "") for f in findings]
    return redact("\n".join(lines))


def scripted_answer(message: str, findings: list[dict]) -> str:
    if not findings:
        return "No diagnostic data yet. Run a diagnostic first."
    actionable = [f for f in findings if f["severity"] != "info"]
    if not actionable:
        return "No problems were detected by the scripted rules."
    m = message.lower()
    pick = [f for f in actionable if f["module"] in m or any(w in m for w in f["title"].lower().split()[:3])] or actionable
    return "Scripted rule-based summary (not AI analysis):\n" + findings_brief(pick) + \
           "\nOpen 'Repair plans' to review proposed fixes; nothing runs without your approval."


async def _claude(message: str, brief: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query  # lazy: optional dependency

    opts = ClaudeAgentOptions(system_prompt=SYSTEM, tools=[], allowed_tools=[], max_turns=1)
    out: list[str] = []
    async for m in query(prompt=f"Findings:\n{brief}\n\nTechnician question: {message}", options=opts):
        if isinstance(m, AssistantMessage):
            out += [b.text for b in m.content if isinstance(b, TextBlock)]
    return "".join(out).strip()


def claude_answer(message: str, findings: list[dict]) -> str:
    """UNTESTED here: needs Claude Code credentials and Internet."""
    return asyncio.run(_claude(redact(message), findings_brief(findings)))
