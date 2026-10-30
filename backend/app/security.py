"""API auth and Windows-protected credential storage."""
from __future__ import annotations

import hmac
from fastapi import Header, HTTPException, Request


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    expected = request.app.state.settings.api_token
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "invalid or missing API token")


from .protect import DpapiProtector, SecretProtector, UnavailableProtector, default_protector  # noqa: F401  (re-exported)
