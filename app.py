"""Fan Control: run the dashboard and one control loop per server.

The code lives in the fanctl package: drivers (how each kind of BMC is read and driven),
control (decisions), server (the control loop and the registry), alerts (Discord) and web (HTTP).
"""

import os
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from fanctl import config, web
from fanctl.alerts import DISCORD_WEBHOOK, WEBHOOK_RE, reporter
from fanctl.server import SERVERS, load_registry


def data_dir_writable():
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.DATA_DIR / ".write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def shutdown(*_):
    for s in list(SERVERS.values()):
        s.log("Stopping: handing fans back to automatic control")
        s.release()
        s.save_history()
    os._exit(0)


def main():
    if not data_dir_writable():
        sys.exit(f"ERROR: cannot write to {config.DATA_DIR.resolve()}. The container runs as uid 1000; "
                 "fix the volume's owner with: chown -R 1000:1000 <host folder>")
    load_registry()
    web.KEY = web.session_key() if config.WEB_PASSWORD else b""
    if DISCORD_WEBHOOK and not WEBHOOK_RE.fullmatch(DISCORD_WEBHOOK):
        print("WARNING: DISCORD_WEBHOOK_URL is not a Discord webhook URL; it is ignored", flush=True)
    if not config.WEB_PASSWORD:
        print("WARNING: WEB_PASSWORD is not set; the dashboard is open to anyone who can reach it", flush=True)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for srv in list(SERVERS.values()):
        srv.start()
    threading.Thread(target=reporter, args=(SERVERS,), daemon=True, name="reporter").start()
    print(f"Fan Control {config.VERSION} listening on :{config.PORT} for {len(SERVERS)} server(s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", config.PORT), web.Handler).serve_forever()


if __name__ == "__main__":
    main()
