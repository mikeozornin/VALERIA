"""Opt-in, isolated user services; never starts or stops the deployed application."""
from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SYSTEMD_INTEGRATION") != "1",
    reason="Set RUN_SYSTEMD_INTEGRATION=1 with a running user systemd manager",
)
ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15)


def wait_new_tree(marker, old_parent=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            pids = json.loads(marker.read_text())
            if pids[0] != old_parent:
                return pids
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    raise AssertionError("Service did not create a new process tree")


def wait_tree_gone(pids):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        live = []
        for pid in pids:
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                if stat.rsplit(")", 1)[1].split()[0] != "Z":
                    live.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not live:
            return
        time.sleep(0.05)
    raise AssertionError(f"Orphaned processes: {live}")


def test_restarts_and_main_sigkill_leave_no_children(tmp_path):
    service = configparser.ConfigParser()
    service.read(ROOT / "deploy/systemd/valeria-web.service")
    unit = f"valeria-lifecycle-test-{uuid.uuid4().hex}.service"
    marker = tmp_path / "processes.json"
    script = tmp_path / "tree.py"
    script.write_text(
        "import json, os, signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-u', '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print(1, flush=True); time.sleep(300)'], "
        "stdout=subprocess.PIPE, start_new_session=True)\n"
        "child.stdout.readline()\n"
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))\n"
        "time.sleep(300)\n"
    )
    properties = []
    for name in ("Type", "KillMode", "TimeoutStopSec", "SendSIGKILL", "Restart", "RestartSec"):
        properties.extend(["--property", f"{name}={service['Service'][name]}"])
    for name in ("StartLimitIntervalSec", "StartLimitBurst"):
        properties.extend(["--property", f"{name}={service['Unit'][name]}"])
    try:
        run(
            "systemd-run", "--user", "--quiet", f"--unit={unit}", *properties,
            sys.executable, str(script), str(marker),
        )
        pids = wait_new_tree(marker)
        # The child's separate session must not let it escape cgroup cleanup.
        assert os.getsid(pids[1]) == pids[1]
        for _ in range(3):
            old = pids
            run("systemctl", "--user", "restart", unit)
            pids = wait_new_tree(marker, old[0])
            wait_tree_gone(old)
        old = pids
        run("systemctl", "--user", "kill", "--kill-who=main", "--signal=KILL", unit)
        pids = wait_new_tree(marker, old[0])
        wait_tree_gone(old)
        run("systemctl", "--user", "stop", unit)
        wait_tree_gone(pids)
    finally:
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            capture_output=True, timeout=60, check=False,
        )
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            capture_output=True, timeout=15, check=False,
        )
