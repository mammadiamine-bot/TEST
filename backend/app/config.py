"""Runtime settings. Every secret comes from the environment, never from source."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CIT_DATA_DIR", Path.home() / ".claude-it-technician")))
    # Per-launch bearer token. The Tauri shell generates it and passes it to the sidecar.
    api_token: str = field(default_factory=lambda: os.environ.get("CIT_API_TOKEN") or secrets.token_urlsafe(32))
    allow_mock: bool = os.environ.get("CIT_ALLOW_MOCK", "1") == "1"
    max_session_minutes: int = 240
    pairing_ttl_seconds: int = 600
    cloud_probe_host: str = os.environ.get("CIT_CLOUD_PROBE_HOST", "api.anthropic.com")
    claude_enabled: bool = os.environ.get("CIT_CLAUDE_ENABLED", "0") == "1"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cit.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"
