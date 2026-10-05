"""Settings that come from the environment, and small helpers every module uses."""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.stdout.reconfigure(errors="replace")  # Windows consoles choke on ° and →

VERSION = os.environ.get("APP_VERSION", "dev")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
VIEW_PASSWORD = os.environ.get("VIEW_PASSWORD", "")   # optional read-only account
TRUST_PROXY = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")  # believe X-Forwarded-For
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")
EMBED_TOKEN = os.environ.get("EMBED_TOKEN", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
INTERVAL = max(5, int(os.environ.get("CHECK_INTERVAL", "15")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
WEB = Path(__file__).parent.parent / "web"

HISTORY_SECONDS = 3 * 3600   # full-resolution history
LONG_BUCKET = 300             # long history: one averaged point per 5 minutes...
LONG_SECONDS = 7 * 86400      # ...kept for 7 days
SAVE_EVERY = 300           # seconds between history snapshots to disk
FAILSAFE_HYSTERESIS = 3    # °C the CPU must drop below the failsafe before manual control resumes
# A control loop that has not turned for this long is stuck (every BMC call has a timeout, the
# slowest 60 s): the watchdog hands the fans back and restarts the process.
STALL_SECONDS = max(180, INTERVAL * 10)


_write_locks = {}
_write_locks_guard = threading.Lock()


def write_json(path, data, private=False):
    """Replace a file atomically and durably: a uniquely named temporary file in the same folder,
    flushed to disk, then renamed over the old one, so a power cut leaves the old file or the new
    one and never half of either, and two writers never share a temporary file. private: mode 600
    from the first byte, for files that hold passwords or tokens."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_locks_guard:
        lock = _write_locks.setdefault(str(path), threading.Lock())
    text = json.dumps(data, separators=(",", ":"))
    with lock:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            if private:
                os.chmod(tmp, 0o600)  # mkstemp already creates it 0600; this keeps that explicit
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if not private:
            try:
                os.chmod(path, 0o644)
            except OSError:
                pass
        try:  # the rename itself must reach the disk too
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # Windows cannot open a folder; the rename is still atomic


def read_json(path, default, what=None, corrupt=None):
    """Read a JSON file. A missing file gives `default`. A corrupt one is kept aside as
    <name>.corrupt-<time> for recovery, reported, and gives `corrupt` (default: `default`), so
    nothing is silently overwritten on top of what might still be rescued."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as e:
        print(f"ERROR cannot read {path.name}: {e}", flush=True)
        return default
    try:
        return json.loads(text)
    except ValueError as e:
        import time
        aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.replace(aside)
        except OSError:
            aside = path
        print(f"ERROR {what or path.name} is corrupt ({e}); kept as {aside.name}", flush=True)
        return default if corrupt is None else corrupt
