"""API auth and Windows-protected credential storage."""
from __future__ import annotations

import hmac
import sys
from typing import Protocol

from fastapi import Header, HTTPException, Request


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    expected = request.app.state.settings.api_token
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "invalid or missing API token")


class SecretProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class DpapiProtector:
    """Windows DPAPI (CryptProtectData), bound to the current Windows user. UNTESTED off-Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI is only available on Windows")

    def _call(self, data: bytes, protect: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = BLOB()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        args = (ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)) if protect else \
               (ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out))
        if not fn(*args):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(data, True)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call(blob, False)


class UnavailableProtector:
    """Non-Windows: refuse to persist secrets rather than store them weakly."""

    def protect(self, data: bytes) -> bytes:
        raise RuntimeError("Windows-protected credential storage is unavailable on this platform")

    unprotect = protect


def default_protector() -> SecretProtector:
    return DpapiProtector() if sys.platform == "win32" else UnavailableProtector()
