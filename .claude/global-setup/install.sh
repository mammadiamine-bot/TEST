#!/usr/bin/env bash
# Installs the orchestrator into ~/.claude without overwriting existing content (backs up first).
set -e
H="$HOME/.claude"; S="$(cd "$(dirname "$0")" && pwd)"; B="$H/backups/pre-orchestrator-$(date +%s)"
mkdir -p "$H/hooks" "$H/skills" "$B"
for f in CLAUDE.md settings.json; do [ -f "$H/$f" ] && cp "$H/$f" "$B/"; done
cp "$S/hooks/orchestrator.py" "$H/hooks/" && chmod +x "$H/hooks/orchestrator.py"
cp -r "$S/skills/intelligent-development-orchestrator" "$H/skills/"
# CLAUDE.md: append once
grep -q "intelligent-development-orchestrator" "$H/CLAUDE.md" 2>/dev/null || { echo >> "$H/CLAUDE.md"; cat "$S/CLAUDE.md" >> "$H/CLAUDE.md"; }
# settings.json: merge hooks (keeps existing keys/hooks)
python3 - "$H/settings.json" "$S/settings.json" <<'PY'
import json,sys,os
p,n=sys.argv[1:]; cur=json.load(open(p)) if os.path.exists(p) else {}
new=json.load(open(n)); hk=cur.setdefault("hooks",{})
for ev,entries in new["hooks"].items():
    have=json.dumps(hk.get(ev,[]))
    for e in entries:
        if "orchestrator.py" not in have: hk.setdefault(ev,[]).append(e)
json.dump(cur,open(p,"w"),indent=2)
PY
echo "Installed. Backup: $B"
