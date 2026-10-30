"""API auth and Windows-protected credential storage."""
from __future__ import annotations

import hmac
from fastapi import Header, HTTPException, Request


def require_token(request: Request) -> None:
    """Authenticate (built-in owner token or a scoped principal's token) and enforce role + organization scope. See tenancy.py."""
    from .tenancy import authorize
    authorize(request)


from .protect import DpapiProtector, SecretProtector, UnavailableProtector, default_protector  # noqa: F401  (re-exported)
