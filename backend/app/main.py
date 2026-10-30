"""Entry point: binds to 127.0.0.1 only. The Tauri shell passes CIT_API_TOKEN and CIT_PORT."""
import os

import uvicorn

from .api import create_app

app = create_app()
app.state.workers.start_monitor()  # reconnects to Worker Stations automatically after network interruptions

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CIT_PORT", "8765")), log_level="warning")
