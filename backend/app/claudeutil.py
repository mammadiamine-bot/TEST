"""Helpers for reading `claude -p --output-format stream-json` output and keeping secrets out of logs."""
from __future__ import annotations

import json

from .targetcore import _KV, _SECRET_VAL


def clean(s: str) -> str:
    return _KV.sub(lambda m: m.group(1) + "=<redacted>", _SECRET_VAL.sub("<redacted>", s))


def claude_event(line: str) -> str | None:
    """Condense one stream-json line from `claude -p --output-format stream-json` into a log line."""
    try:
        j = json.loads(line)
    except json.JSONDecodeError:
        return line[:300] if line.strip() else None
    t = j.get("type")
    if t == "assistant":
        out = []
        for b in (j.get("message") or {}).get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append(b["text"].strip().replace("\n", " ")[:300])
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                out.append(f"tool {b.get('name')}: {inp.get('file_path') or inp.get('pattern') or inp.get('command') or ''}"[:200])
        return " | ".join(out) or None
    if t == "result":
        return f"result: {'ERROR ' if j.get('is_error') else ''}{str(j.get('result', ''))[:300]}" + (f" (cost ${j['total_cost_usd']:.4f})" if j.get("total_cost_usd") else "")
    return None


