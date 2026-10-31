"""Entry point: binds to 127.0.0.1 unless CIT_HOST says otherwise (the Docker image sets it behind the reverse proxy). The Tauri shell passes CIT_API_TOKEN and CIT_PORT."""
import json
import logging
import os
import sys
import time

import uvicorn

from .api import create_app

app = create_app()
app.state.workers.start_monitor()
app.state.agent.start_ticker()  # advances AI technician sessions after approvals
app.state.ops.start_scheduler()  # runs due schedules  # reconnects to Worker Stations automatically after network interruptions



class JsonFormatter(logging.Formatter):
    """One JSON object per line (machine-parseable). Messages never carry request bodies or tokens."""
    def format(self, r: logging.LogRecord) -> str:
        return json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname, "logger": r.name, "msg": r.getMessage()})


def configure_logging() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if os.environ.get("CIT_LOG_FORMAT", "json") == "json" else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=os.environ.get("CIT_LOG_LEVEL", "WARNING"), handlers=[h], force=True)


if __name__ == "__main__":
    configure_logging()
    uvicorn.run(app, host=os.environ.get("CIT_HOST", "127.0.0.1"), port=int(os.environ.get("CIT_PORT", "8765")), log_level=os.environ.get("CIT_LOG_LEVEL", "warning").lower(), log_config=None)
