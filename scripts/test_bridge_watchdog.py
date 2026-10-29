"""Tests for bridge-watchdog.ps1 (issue #48): a launcher that hangs before
starting the bridge is stopped by its PID and started again, a slow bridge is
left alone, and nothing is started when the port is already up.

Drives the real script through Windows PowerShell 5.1 against throwaway
launchers on a free port, with a short -GraceSeconds. The "bridge" is either a
tiny Python listener or a copy of ping.exe renamed whatsapp-bridge.exe, so the
watchdog's child lookup sees the real process name. Windows only.

Run: python -m unittest scripts/test_bridge_watchdog.py -v
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

WATCHDOG = Path(__file__).resolve().parent.parent / "bridge-watchdog.ps1"
GRACE = 6

LISTENER = (
    "import socket,sys,time,os;"
    "s=socket.socket();s.bind(('127.0.0.1',int(sys.argv[1])));s.listen();"
    "open(sys.argv[2],'w').write(str(os.getpid()));time.sleep(120)"
)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pid_alive(pid):
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    ).stdout
    return str(pid) in out


def kill_pid(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


@unittest.skipUnless(sys.platform == "win32", "bridge-watchdog.ps1 is the Windows auto-start")
class BridgeWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="watchdog-test-"))
        self.port = free_port()
        self.log = self.dir / "watchdog.log"
        self.to_kill = []

    def tearDown(self):
        for pid in self.to_kill:
            kill_pid(pid)
        shutil.rmtree(self.dir, ignore_errors=True)

    def launcher(self, body):
        path = self.dir / "start-bridge.ps1"
        path.write_text(body, encoding="ascii")
        return path

    def run_watchdog(self, launcher, attempts=3):
        # Output goes to a file, not a pipe: the launcher and the "bridge"
        # inherit the handles and outlive the watchdog, so reading a pipe to
        # EOF would wait for them instead of for the watchdog.
        with open(self.dir / "stderr.txt", "w") as err:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(WATCHDOG),
                    "-Launcher", str(launcher), "-Port", str(self.port),
                    "-Log", str(self.log),
                    "-GraceSeconds", str(GRACE), "-MaxAttempts", str(attempts),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                timeout=GRACE * attempts + 60,
            )
        result.stderr = (self.dir / "stderr.txt").read_text(errors="replace")
        return result

    def log_text(self):
        return self.log.read_text(encoding="ascii") if self.log.exists() else ""

    def test_stuck_launcher_is_stopped_by_pid_and_restarted(self):
        marker = self.dir / "first-run"
        listener_pid = self.dir / "listener.pid"
        launcher = self.launcher(
            f"if (-not (Test-Path '{marker}')) {{\n"
            f"    New-Item -ItemType File '{marker}' | Out-Null\n"
            f"    Start-Sleep -Seconds 600\n"
            f"}}\n"
            f"& '{sys.executable}' -c \"{LISTENER}\" {self.port} '{listener_pid}'\n"
        )
        result = self.run_watchdog(launcher)
        if listener_pid.exists():
            self.to_kill.append(int(listener_pid.read_text()))
        log = self.log_text()

        self.assertEqual(result.returncode, 0, log + result.stderr)
        stuck = re.search(r"launcher pid (\d+) stuck for", log)
        self.assertIsNotNone(stuck, log)
        self.assertFalse(pid_alive(int(stuck.group(1))), "stuck launcher still alive")
        self.assertIn("attempt 2/3 started launcher", log)
        self.assertIn("port up", log)

    def test_gives_up_with_exit_1_when_every_attempt_hangs(self):
        launcher = self.launcher("Start-Sleep -Seconds 600\n")
        result = self.run_watchdog(launcher, attempts=2)
        log = self.log_text()

        self.assertEqual(result.returncode, 1, log)
        self.assertEqual(len(re.findall(r"stuck for", log)), 2, log)
        for pid in re.findall(r"launcher pid (\d+) stuck", log):
            self.assertFalse(pid_alive(int(pid)), f"launcher {pid} left running")
        self.assertIn("gave up after 2 attempts", log)

    def test_slow_bridge_is_left_running(self):
        fake_bridge = self.dir / "whatsapp-bridge.exe"
        shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE", fake_bridge)
        launcher = self.launcher(f"& '{fake_bridge}' -n 600 127.0.0.1 | Out-Null\n")
        result = self.run_watchdog(launcher)
        log = self.log_text()
        bridge = re.search(r"bridge pid (\d+) is running; leaving it", log)
        if bridge:
            self.to_kill.append(int(bridge.group(1)))

        self.assertEqual(result.returncode, 0, log)
        self.assertIsNotNone(bridge, log)
        self.assertTrue(pid_alive(int(bridge.group(1))), "slow bridge was killed")
        self.assertNotIn("stopping it", log)
        self.assertNotIn("attempt 2/", log)

    def test_launcher_exit_code_is_logged_and_retried(self):
        launcher = self.launcher("exit 7\n")
        result = self.run_watchdog(launcher, attempts=2)
        log = self.log_text()

        self.assertEqual(result.returncode, 1, log)
        self.assertEqual(log.count("exited with code 7 before the port came up"), 2, log)
        self.assertIn("gave up after 2 attempts", log)

    def test_nothing_started_when_port_already_listening(self):
        marker = self.dir / "launcher-ran"
        launcher = self.launcher(f"New-Item -ItemType File '{marker}' | Out-Null\n")
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.port))
            s.listen()
            result = self.run_watchdog(launcher)
        log = self.log_text()

        self.assertEqual(result.returncode, 0, log)
        self.assertIn("nothing to start", log)
        time.sleep(1)
        self.assertFalse(marker.exists(), "launcher ran although the port was up")


if __name__ == "__main__":
    unittest.main()
