"""Fan Control: run the dashboard and one control loop per server.

The code lives in the fanctl package: drivers (how each kind of BMC is read and driven),
control (decisions), server (the control loop and the registry), alerts (Discord) and web (HTTP).
"""

import logging
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from fanctl import config, logs, updates, web
from fanctl.alerts import DISCORD_WEBHOOK, WEBHOOK_RE, reporter
from fanctl.server import SERVERS, load_registry, release_all, stalled


log = logging.getLogger("fanctl")


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
    """docker stop: every fan back to its BMC, all servers at once, well inside the grace period
    (stop_grace_period: 60s in the compose file; Docker's default is 10 s)."""
    for s in list(SERVERS.values()):
        s.log("Stopping: handing fans back to automatic control")
    failed = release_all(SERVERS.values(), deadline=40)
    if failed:
        log.error("could not hand the fans back for %s", ", ".join(failed))
    for s in list(SERVERS.values()):
        s.save_history()
    os._exit(0)


def watchdog():
    """A control loop that stops turning leaves the fans wherever it last set them, with nobody
    watching the temperature. Hand every fan back to its BMC and exit: Docker's restart policy
    starts a fresh process."""
    while True:
        time.sleep(30)
        stuck = stalled(SERVERS.values())
        if stuck:
            log.error("control loop stalled for %s; handing fans back and restarting", ", ".join(stuck))
            release_all(SERVERS.values(), deadline=20)  # a stuck loop's lock is not waited on for long
            os._exit(1)


class BoundedServer(ThreadingHTTPServer):
    """A thread per request, but never more than MAX_REQUESTS at once: a flood of slow or idle
    connections waits its turn instead of using up threads and memory."""
    MAX_REQUESTS = 64
    daemon_threads = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.slots = threading.BoundedSemaphore(self.MAX_REQUESTS)

    def process_request(self, request, client_address):
        if not self.slots.acquire(timeout=10):
            self.shutdown_request(request)  # busy for 10 s: drop this one rather than queue forever
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    logs.setup()
    if not data_dir_writable():
        sys.exit(f"ERROR: cannot write to {config.DATA_DIR.resolve()}. The container runs as uid 1000; "
                 "fix the volume's owner with: chown -R 1000:1000 <host folder>")
    load_registry()
    web.KEY = web.session_key() if config.WEB_PASSWORD else b""
    if DISCORD_WEBHOOK and not WEBHOOK_RE.fullmatch(DISCORD_WEBHOOK):
        log.warning("DISCORD_WEBHOOK_URL is not a Discord webhook URL; it is ignored")
    if not config.WEB_PASSWORD:
        log.warning("WEB_PASSWORD is not set; the dashboard is open to anyone who can reach it")
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for srv in list(SERVERS.values()):
        srv.start()
    threading.Thread(target=reporter, args=(SERVERS,), daemon=True, name="reporter").start()
    threading.Thread(target=watchdog, daemon=True, name="watchdog").start()
    threading.Thread(target=updates.loop, daemon=True, name="updates").start()
    log.info("Fan Control %s listening on :%s for %d server(s)", config.VERSION, config.PORT, len(SERVERS))
    # every interface inside the container; what reaches it is decided by the published port
    BoundedServer(("0.0.0.0", config.PORT), web.Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
