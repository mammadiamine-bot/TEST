"""Multi-client tenancy: organizations (clients) -> sites -> devices / workers, principals with roles, DEFAULT-DENY scoping.

- The built-in CIT_API_TOKEN stays the unrestricted owner (nothing existing breaks).
- Other principals (technician / viewer) get their own token (stored only as a SHA-256 hash, shown once) and a list of organizations.
- A scoped principal may touch a resource only if the resource's organization is in its list. Anything not explicitly recognised is denied,
  and unassigned resources (org_id NULL) are visible to the owner only. Denials are written to the audit chain with the principal's id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

ROLES = ("administrator", "technician", "viewer", "customer")  # "owner" exists only as the built-in token; it cannot be minted through the API (no privilege escalation)
READ_OPEN = {"/status", "/features", "/stacks", "/task-kinds", "/tools", "/me"}  # not tenant data
# resource kind -> table whose device_id/org_id gives the owning organization
DEVICE_CHILD = {"projects": "projects", "tasks": "tasks", "plans": "plans", "runs": "runs", "sessions": "sessions"}
RESOURCE = re.compile(r"^/(devices|workers|projects|tasks|plans|runs|sessions)/([^/]+)")
ORG = re.compile(r"^/orgs/([^/]+)")
OWNER_ONLY_ANY = re.compile(r"^/principals")  # listing/creating who has access: owner, or an administrator inside their own organizations (handlers narrow it further)
OWNER_ONLY_WRITE = re.compile(r"^(/orgs|/orgs/[^/]+/subscription)$")  # creating clients and billing changes
OWNER_ONLY_ALL = re.compile(r"^/orgs/[^/]+/(offboard|purge)$")
ADMIN_WRITE = re.compile(r"^(/orgs/[^/]+/(sites|invitations|groups|alert-rules|notify-policies|settings)(/.*)?|/(devices|workers)/[^/]+/(assign|tags))$")
ADMIN_READ = re.compile(r"^/orgs/[^/]+/(audit|invitations|notifications)$")
CUSTOMER_GET = re.compile(r"^/orgs/[^/]+(/fleet|/alerts|/tickets(/[^/]+)?|/reports/[^/]+|/subscription)?$")
CUSTOMER_POST = re.compile(r"^/orgs/[^/]+/tickets$")
FAIL_LIMIT, FAIL_WINDOW = 30, 60.0  # failed authentications per client address per minute


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


@dataclass
class Principal:
    id: str
    role: str
    orgs: frozenset[str] | None  # None = unrestricted (built-in owner only)

    @property
    def is_owner(self) -> bool:
        return self.orgs is None


def _fails(request: Request):
    st = request.app.state
    if not hasattr(st, "authfail"):
        st.authfail = {}
    return st.authfail.setdefault(request.client.host if request.client else "?", [])


def authenticate(request: Request) -> Principal:
    fails = _fails(request)
    now = time.time()
    fails[:] = [t for t in fails if now - t < FAIL_WINDOW]
    if len(fails) >= FAIL_LIMIT:
        raise HTTPException(429, "too many failed attempts; try again later")

    def bad():
        fails.append(now)
        raise HTTPException(401, "invalid or missing API token")
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        bad()
    if hmac.compare_digest(supplied.encode(), request.app.state.settings.api_token.encode()):
        return Principal("owner", "owner", None)
    db = request.app.state.core.db
    row = db.one("SELECT * FROM principals WHERE token_hash=? AND revoked=0", (token_hash(supplied),))
    if not row:
        bad()
    db.execute("UPDATE principals SET last_used=? WHERE id=?", (now, row["id"]))
    return Principal(row["id"], row["role"], frozenset(json.loads(row["org_ids"])))


def org_of(db, kind: str, rid: str) -> str | None:
    if kind in ("devices", "workers"):
        r = db.one(f"SELECT org_id FROM {kind} WHERE id=?", (rid,))
    else:
        r = db.one(f"SELECT d.org_id AS org_id FROM {DEVICE_CHILD[kind]} t JOIN devices d ON d.id=t.device_id WHERE t.id=?", (rid,))
    return r["org_id"] if r else None


def org_active(db, oid: str) -> bool:
    r = db.one("SELECT status FROM orgs WHERE id=?", (oid,))
    return bool(r) and (r["status"] or "active") == "active"


def authorize(request: Request) -> None:
    """FastAPI dependency (wired through security.require_token): authenticate, then enforce role + organization scope."""
    p = authenticate(request)
    request.state.principal = p
    if p.is_owner:
        return
    db, path, method = request.app.state.core.db, request.url.path, request.method
    write = method not in ("GET", "HEAD")

    def deny(why: str):
        db.audit(p.id, "tenancy.denied", None, method=method, path=path, why=why)
        raise HTTPException(403, "not permitted")  # same answer for 'forbidden' and 'does not exist': no probing

    def in_scope(oid: str, why: str):
        if oid not in p.orgs or not org_active(db, oid):
            deny(why)

    if not write and path in ("/me", "/orgs"):
        return  # own identity / own (filtered) organization list
    if OWNER_ONLY_ALL.match(path) or (write and OWNER_ONLY_WRITE.match(path)):
        deny("owner only")
    if OWNER_ONLY_ANY.match(path):
        if p.role != "administrator":
            deny("administrator or owner only")
        return  # handlers confine an administrator to their own organizations
    if p.role in ("viewer", "customer") and write and not (p.role == "customer" and CUSTOMER_POST.match(path)):
        deny("read-only role")
    if p.role == "customer":
        if (CUSTOMER_GET.match(path) and not write) or CUSTOMER_POST.match(path):
            in_scope(ORG.match(path).group(1), "organization out of scope")
            return
        deny("route not available to customers")
    if write and ADMIN_WRITE.match(path) and p.role != "administrator":
        deny("administrator only")
    if not write and ADMIN_READ.match(path) and p.role != "administrator":
        deny("administrator only")
    if not write and (path in READ_OPEN or path == "/orgs"):
        return
    m = ORG.match(path)
    if m:
        in_scope(m.group(1), "organization out of scope")
        return
    m = RESOURCE.match(path)
    if m:
        org = org_of(db, m.group(1), m.group(2))
        if org is not None and org in p.orgs and org_active(db, org):
            return
        deny("resource out of scope or unassigned")
    deny("route not available to scoped principals")


def mint_principal(db, name: str, role: str, org_ids: list[str], actor: str) -> tuple[dict, str]:
    from .db import new_id
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if not org_ids or any(not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (o,)) for o in org_ids):
        raise HTTPException(422, "at least one existing organization is required; unrestricted principals cannot be created")
    tok = "cit_" + secrets.token_urlsafe(32)
    pid = new_id("usr")
    db.execute("INSERT INTO principals(id,name,role,org_ids,token_hash,created_at) VALUES(?,?,?,?,?,?)",
               (pid, name[:80], role, json.dumps(sorted(set(org_ids))), token_hash(tok), time.time()))
    db.audit(actor, "principal.created", None, principal=pid, role=role, orgs=sorted(set(org_ids)))
    return {"id": pid, "name": name, "role": role, "org_ids": sorted(set(org_ids))}, tok
