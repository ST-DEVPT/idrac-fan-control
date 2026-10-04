"""Test environment: two demo servers from the environment, a throw-away data folder.
Run every test with:  python -m unittest"""
import atexit
import os
import shutil
import tempfile

DATA = tempfile.mkdtemp(prefix="fanctl-test-")
atexit.register(shutil.rmtree, DATA, ignore_errors=True)
os.environ.update(DATA_DIR=DATA, WEB_PASSWORD="hunter2", VIEW_PASSWORD="lookonly", METRICS_TOKEN="m-token", EMBED_TOKEN="e-token",
                  IDRAC_1_HOST="demo", IDRAC_1_NAME="Rack A", IDRAC_2_HOST="demo", IDRAC_2_NAME="Rack B")

from fanctl import server, web  # noqa: E402

server.load_registry()
web.KEY = web.session_key()
