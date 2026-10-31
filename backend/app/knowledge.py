"""Local knowledge: verified cases, versioned procedures, similarity search, feedback, import/export. No model weights are ever trained.

Trust rules (what keeps the agent from "learning" nonsense):
* A case is 'verified' only when a repair was run AND its outcome check passed. Everything else is 'unverified' and is shown but never
  used to pick or rank an action. A person can reject a case, which removes it from reuse.
* A procedure maps a problem signature to a workflow that ALREADY exists in repairs.LIBRARY. It can never contain code that runs, never
  changes a workflow's risk class, and stays 'proposed' (inert) until an administrator approves it. Procedure-derived repairs always
  need approval - only the built-in mapping may run automatically.
* Imported bundles are validated and land as 'proposed'/'unverified', whoever wrote them (Claude, another tool, a colleague).
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter

from .db import Db, new_id
from .repairs import LIBRARY
from .service import CoreError

FORMAT, VERSION = "cit-knowledge", 1
STOP = set("the a an is are my our to of and or in on it this that i you please can could why what how not with for from has have was were be been do does did".split())
WORD = re.compile(r"[a-z0-9]{2,}|[؀-ۿ]{2,}")
CODE = re.compile(r"^[a-z0-9_]{2,40}$")


def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall((text or "").lower()) if w not in STOP]


class Knowledge:
    def __init__(self, db: Db):
        self.db = db

    # ---- cases ---------------------------------------------------------------------------------------------------------------------
    def record_case(self, org: str, *, session_id: str, os_: str | None, host: str | None, request: str, signature: list[str], root_cause: str | None, causes: list[dict],
                    evidence: dict, workflow: str | None, outcome: str, verified: bool, confidence: float) -> str:
        cid = new_id("case")
        status = "verified" if verified else "unverified"
        toks = " ".join(tokens(request) + ["sig_" + s for s in signature for _ in range(3)])
        self.db.execute("INSERT INTO kb_cases(id,org_id,os,host,request,signature,tokens,root_cause,causes,evidence,workflow,outcome,verified,confidence,feedback,status,session_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                        (cid, org, os_, host, request[:400], " ".join(sorted(set(signature))), toks, root_cause, json.dumps(causes)[:4000], json.dumps(evidence)[:6000], workflow, outcome, int(verified), confidence, status, session_id, time.time()))
        return cid

    def similar(self, org: str, request: str, signature: list[str], k: int = 3, min_score: float = 0.25) -> list[dict]:
        q = Counter(tokens(request) + ["sig_" + s for s in signature for _ in range(3)])
        if not q:
            return []
        qn = math.sqrt(sum(v * v for v in q.values()))
        scored = []
        for c in self.db.all("SELECT * FROM kb_cases WHERE org_id=? AND status!='rejected' ORDER BY created_at DESC LIMIT 500", (org,)):
            t = Counter((c["tokens"] or "").split())
            if not t:
                continue
            dot = sum(q[w] * t[w] for w in q if w in t)
            s = dot / (qn * math.sqrt(sum(v * v for v in t.values()))) if dot else 0
            if s >= min_score:
                scored.append({"score": round(s, 2), "id": c["id"], "request": c["request"], "root_cause": c["root_cause"], "workflow": c["workflow"], "outcome": c["outcome"], "status": c["status"], "confidence": c["confidence"], "created_at": c["created_at"], "feedback": c["feedback"]})
        return sorted(scored, key=lambda x: -x["score"])[:k]

    def stats(self, org: str, code: str, workflow: str) -> dict:
        """How this workflow has fared for this problem code in this client's VERIFIED history (and failures of any status)."""
        ok = bad = 0
        for c in self.db.all("SELECT outcome,verified,status,feedback FROM kb_cases WHERE org_id=? AND workflow=? AND (' '||signature||' ') LIKE ? AND status!='rejected'", (org, workflow, f"% {code} %")):
            if c["verified"] and c["outcome"] == "resolved":
                ok += 1 + max(0, c["feedback"])
            elif c["outcome"] in ("failed", "unresolved"):
                bad += 1 + max(0, -c["feedback"])
        return {"ok": ok, "bad": bad, "confidence": round((ok + 0.5) / (ok + bad + 1), 2)}

    def cases(self, org: str, limit: int = 100) -> list[dict]:
        return self.db.all("SELECT id,os,host,request,signature,root_cause,workflow,outcome,verified,confidence,feedback,status,created_at FROM kb_cases WHERE org_id=? ORDER BY created_at DESC LIMIT ?", (org, limit))

    def case(self, org: str, cid: str) -> dict:
        c = self.db.one("SELECT * FROM kb_cases WHERE id=? AND org_id=?", (cid, org))
        if not c:
            raise CoreError("unknown case", 404)
        return {**c, "causes": json.loads(c["causes"] or "[]"), "evidence": json.loads(c["evidence"] or "{}")}

    def feedback(self, org: str, cid: str, delta: int, actor: str, note: str = "") -> dict:
        c = self.case(org, cid)
        if delta not in (-1, 1):
            raise CoreError("feedback must be +1 or -1", 422)
        status = "rejected" if delta < 0 else c["status"]
        self.db.execute("UPDATE kb_cases SET feedback=feedback+?, status=? WHERE id=?", (delta, status, cid))
        self.db.audit(actor, "kb.case_feedback", None, org=org, case=cid, delta=delta, note=note[:120])
        return self.case(org, cid)

    # ---- procedures ----------------------------------------------------------------------------------------------------------------
    def builtin(self) -> list[dict]:
        return [{"id": "builtin:" + w.id, "key": w.id, "version": 1, "title": w.title, "signature": list(w.signature), "workflow": w.id, "status": "approved", "source": "builtin", "cls": w.cls,
                 "body": f"{w.expected}\n\nPrecondition: {w.precondition}\nRollback: {w.rollback or 'none'}"} for w in LIBRARY.values()]

    def procedures(self, org: str) -> list[dict]:
        rows = [{**r, "signature": json.loads(r["signature"])} for r in self.db.all("SELECT * FROM kb_procedures WHERE org_id=? ORDER BY key, version DESC", (org,))]
        return self.builtin() + rows

    def approved_for(self, org: str, code: str) -> list[dict]:
        return [p for p in self.procedures(org) if p["source"] != "builtin" and p["status"] == "approved" and code in p["signature"] and p.get("workflow") in LIBRARY]

    def _validate(self, p: dict) -> dict:
        if not isinstance(p, dict):
            raise CoreError("each procedure must be an object", 422)
        key, title = str(p.get("key", "")).strip(), str(p.get("title", "")).strip()
        sig = p.get("signature") or []
        if not re.fullmatch(r"[a-z0-9_.-]{2,48}", key) or not (3 <= len(title) <= 120):
            raise CoreError("a procedure needs a key (a-z, 0-9, _ . -) and a title", 422)
        if not isinstance(sig, list) or not sig or len(sig) > 8 or not all(isinstance(c, str) and CODE.match(c) for c in sig):
            raise CoreError("signature must be 1-8 problem codes", 422)
        wf = p.get("workflow")
        if wf is not None and wf not in LIBRARY:
            raise CoreError(f"workflow '{wf}' is not in the reviewed repair library; procedures cannot add new executable actions", 422)
        body = str(p.get("body", ""))
        if len(body) > 8000:
            raise CoreError("procedure text is too long (max 8000 characters)", 422)
        return {"key": key, "title": title, "signature": sig, "workflow": wf, "body": body}

    def propose(self, org: str, actor: str, p: dict, source: str = "manual") -> dict:
        v = self._validate(p)
        ver = (self.db.one("SELECT MAX(version) AS v FROM kb_procedures WHERE org_id=? AND key=?", (org, v["key"])) or {}).get("v") or 0
        pid = new_id("proc")
        self.db.execute("INSERT INTO kb_procedures(id,org_id,key,version,title,signature,workflow,body,status,source,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,'proposed',?,?,?)",
                        (pid, org, v["key"], ver + 1, v["title"], json.dumps(v["signature"]), v["workflow"], v["body"], source, actor, time.time()))
        self.db.audit(actor, "kb.procedure_proposed", None, org=org, procedure=pid, key=v["key"], version=ver + 1, source=source)
        return self.procedure(org, pid)

    def procedure(self, org: str, pid: str) -> dict:
        r = self.db.one("SELECT * FROM kb_procedures WHERE id=? AND org_id=?", (pid, org))
        if not r:
            raise CoreError("unknown procedure", 404)
        return {**r, "signature": json.loads(r["signature"])}

    def decide(self, org: str, pid: str, approve: bool, actor: str, is_owner: bool, same_person) -> dict:
        r = self.procedure(org, pid)
        if r["status"] != "proposed":
            raise CoreError("this procedure is not waiting for review", 409)
        if approve and not is_owner and r["created_by"] and same_person(r["created_by"], actor):
            raise CoreError("a second person must approve a procedure (four-eyes rule)", 403)
        if approve:   # a new approved version retires the previous one: exactly one live version per key
            self.db.execute("UPDATE kb_procedures SET status='retired' WHERE org_id=? AND key=? AND status='approved'", (org, r["key"]))
        self.db.execute("UPDATE kb_procedures SET status=?, approved_by=? WHERE id=?", ("approved" if approve else "rejected", actor if approve else None, pid))
        self.db.audit(actor, "kb.procedure_approved" if approve else "kb.procedure_rejected", None, org=org, procedure=pid, key=r["key"], version=r["version"])
        return self.procedure(org, pid)

    def retire(self, org: str, pid: str, actor: str) -> dict:
        self.procedure(org, pid)
        self.db.execute("UPDATE kb_procedures SET status='retired' WHERE id=?", (pid,))
        self.db.audit(actor, "kb.procedure_retired", None, org=org, procedure=pid)
        return self.procedure(org, pid)

    # ---- portable format -----------------------------------------------------------------------------------------------------------
    def export(self, org: str) -> dict:
        procs = [{k: p[k] for k in ("key", "version", "title", "signature", "workflow", "body", "status")} for p in self.procedures(org) if p["source"] != "builtin"]
        cases = [{k: c[k] for k in ("os", "request", "signature", "root_cause", "workflow", "outcome", "verified", "confidence", "status")} for c in self.db.all("SELECT * FROM kb_cases WHERE org_id=? AND status!='rejected'", (org,))]
        return {"format": FORMAT, "version": VERSION, "exported_at": time.time(), "procedures": procs, "cases": cases, "note": "No computer names, addresses or secrets are included. Imports land as proposed/unverified."}

    def import_(self, org: str, actor: str, bundle: dict, restore: bool = False) -> dict:
        """Validate everything first (all-or-nothing). restore=True (owner only, caller checks) keeps statuses from a CIT backup of this system."""
        if not isinstance(bundle, dict) or bundle.get("format") != FORMAT or bundle.get("version") != VERSION:
            raise CoreError(f"not a CIT knowledge bundle (expected format '{FORMAT}' version {VERSION})", 422)
        procs, cases = bundle.get("procedures") or [], bundle.get("cases") or []
        if not isinstance(procs, list) or not isinstance(cases, list) or len(procs) > 200 or len(cases) > 2000:
            raise CoreError("bundle too large or malformed (max 200 procedures, 2000 cases)", 422)
        clean_p = [self._validate(p) | {"_status": p.get("status")} for p in procs]
        clean_c = []
        for c in cases:
            if not isinstance(c, dict):
                raise CoreError("each case must be an object", 422)
            sig = [s for s in (str(c.get("signature", "")).split()) if CODE.match(s)][:8]
            wf = c.get("workflow") if c.get("workflow") in LIBRARY else None
            clean_c.append({"request": str(c.get("request", ""))[:400], "sig": sig, "root_cause": str(c.get("root_cause") or "")[:200], "workflow": wf, "outcome": str(c.get("outcome") or "unresolved")[:20], "os": str(c.get("os") or "")[:40],
                            "verified": bool(c.get("verified")) and restore, "confidence": float(c.get("confidence") or 0) if restore else 0.0})
        for p in clean_p:
            ver = (self.db.one("SELECT MAX(version) AS v FROM kb_procedures WHERE org_id=? AND key=?", (org, p["key"])) or {}).get("v") or 0
            st = "approved" if restore and p["_status"] == "approved" else "proposed"
            self.db.execute("INSERT INTO kb_procedures(id,org_id,key,version,title,signature,workflow,body,status,source,created_by,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,'import',?,?,?)",
                            (new_id("proc"), org, p["key"], ver + 1, p["title"], json.dumps(p["signature"]), p["workflow"], p["body"], st, actor, actor if st == "approved" else None, time.time()))
        for c in clean_c:
            self.record_case(org, session_id="import", os_=c["os"], host=None, request=c["request"], signature=c["sig"], root_cause=c["root_cause"], causes=[], evidence={}, workflow=c["workflow"], outcome=c["outcome"], verified=c["verified"], confidence=c["confidence"])
        self.db.audit(actor, "kb.imported", None, org=org, procedures=len(clean_p), cases=len(clean_c), restore=restore)
        return {"procedures": len(clean_p), "cases": len(clean_c), "status": "approved procedures restored" if restore else "imported as proposed/unverified - review before use"}
