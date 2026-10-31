"""Runtime settings. Every secret comes from the environment, never from source."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _owner_token() -> str:
    """CIT_API_TOKEN_FILE (a Docker/Windows secret file) wins over CIT_API_TOKEN. In production (CIT_ENV=production) a weak token is refused."""
    f = os.environ.get("CIT_API_TOKEN_FILE")
    tok = (Path(f).read_text().strip() if f else os.environ.get("CIT_API_TOKEN")) or secrets.token_urlsafe(32)
    if os.environ.get("CIT_ENV") == "production" and len(tok) < 32:
        raise SystemExit("refusing to start: the owner token must be at least 32 characters in production")
    return tok


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CIT_DATA_DIR", Path.home() / ".claude-it-technician")))
    # Per-launch bearer token. The Tauri shell generates it and passes it to the sidecar.
    api_token: str = field(default_factory=_owner_token)
    allow_mock: bool = os.environ.get("CIT_ALLOW_MOCK", "1") == "1"
    max_session_minutes: int = 240
    pairing_ttl_seconds: int = 600
    cloud_probe_host: str = os.environ.get("CIT_CLOUD_PROBE_HOST", "api.anthropic.com")
    ssh_bin: str = os.environ.get("CIT_SSH_BIN", "ssh")
    local_roots: list = field(default_factory=lambda: [r for r in os.environ.get("CIT_LOCAL_ROOTS", "").split(os.pathsep) if r])
    claude_enabled: bool = os.environ.get("CIT_CLAUDE_ENABLED", "0") == "1"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cit.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"
