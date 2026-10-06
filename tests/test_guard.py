"""scripts/fan-guard.sh against a fake docker and a fake ipmitool: it acts only from the second bad
minute, finds the container by its image, leaves a removed container alone, acts when Docker is down,
uses each vendor's command, keeps passwords off the command line and skips names it cannot trust."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).parent.parent / "scripts" / "fan-guard.sh"

DOCKER = """#!/bin/sh
case "$1" in
  info) [ "$FAKE_DOWN" = 1 ] && exit 1; exit 0 ;;
  ps) [ -n "$FAKE_PS" ] && printf '%s\\n' "$FAKE_PS"; exit 0 ;;
  inspect) [ "$FAKE_STATUS" = none ] && exit 1; echo "$FAKE_STATUS" ;;
esac
"""
IPMITOOL = """#!/bin/sh
echo "$* password=$IPMI_PASSWORD" >> "$CALLS"
"""
ENV = """GUARD_SERVERS="R420 X11 bad;rm"
R420_HOST=10.0.0.1
R420_PASSWORD="p w"
X11_HOST=10.0.0.2
X11_USERNAME=ADMIN
X11_PASSWORD=x
X11_DRIVER=supermicro
"""


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "needs a POSIX shell")
class FanGuard(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fan-guard-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        bin_ = self.dir / "bin"
        bin_.mkdir()
        for name, body in (("docker", DOCKER), ("ipmitool", IPMITOOL)):
            (bin_ / name).write_text(body)
            (bin_ / name).chmod(0o755)
        (self.dir / ".env").write_text(ENV)
        (self.dir / ".env").chmod(0o600)
        self.calls = self.dir / "calls"

    def run_guard(self, status="running healthy", ps="fan-control ghcr.io/st-devpt/rack-fan-control:2", down=False):
        env = {"PATH": f"{self.dir / 'bin'}:/usr/bin:/bin", "ENV_FILE": str(self.dir / ".env"),
               "STATE": str(self.dir / "state"), "CALLS": str(self.calls), "FAKE_STATUS": status, "FAKE_PS": ps,
               "FAKE_DOWN": "1" if down else "0"}
        return subprocess.run(["sh", str(GUARD)], env=env, capture_output=True, text=True, check=True).stdout

    def sent(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_healthy_does_nothing(self):
        self.run_guard()
        self.run_guard()
        self.assertEqual(self.sent(), [])

    def test_dead_container_from_the_second_minute(self):
        self.run_guard("exited ")
        self.assertEqual(self.sent(), [])                       # one bad minute may be a restart
        out = self.run_guard("exited ")
        self.assertEqual(self.sent(), ["-I lanplus -H 10.0.0.1 -U root -E raw 0x30 0x30 0x01 0x01 password=p w",
                                       "-I lanplus -H 10.0.0.2 -U ADMIN -E raw 0x30 0x45 0x01 0x02 password=x"])
        self.assertIn("skipping invalid server name 'bad;rm'", out)
        self.run_guard()                                        # healthy again: the count starts over
        self.run_guard("exited ")
        self.assertEqual(len(self.sent()), 2)

    def test_removed_or_never_deployed_is_left_alone(self):
        for _ in range(3):
            self.run_guard("none")
            self.run_guard("exited ", ps="")
        self.assertEqual(self.sent(), [])

    def test_docker_down_counts_as_dead(self):
        self.run_guard(down=True)
        self.run_guard(down=True)
        self.assertEqual(len(self.sent()), 2)

    def test_env_file_writable_by_others_is_refused(self):
        (self.dir / ".env").chmod(0o666)
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_guard("exited ")
