"""Settings that come from the environment, and small helpers every module uses."""

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")  # Windows consoles choke on ° and →

VERSION = os.environ.get("APP_VERSION", "dev")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")
EMBED_TOKEN = os.environ.get("EMBED_TOKEN", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
INTERVAL = max(5, int(os.environ.get("CHECK_INTERVAL", "15")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
WEB = Path(__file__).parent.parent / "web"

HISTORY_SECONDS = 3 * 3600
SAVE_EVERY = 300           # seconds between history snapshots to disk
FAILSAFE_HYSTERESIS = 3    # °C the CPU must drop below the failsafe before manual control resumes


def write_json(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(path)
