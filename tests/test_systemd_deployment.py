from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_guard_refuses_existing_server_without_printing_arguments(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    guard = deploy / "check-legacy-processes.py"
    shutil.copy2(ROOT / "deploy/check-legacy-processes.py", guard)
    web = tmp_path / ".venv/bin/opencode-web"
    web.parent.mkdir(parents=True)
    web.write_text("import time; time.sleep(60)")
    process = subprocess.Popen(
        [sys.executable, str(web), "do-not-print-this-secret"], cwd=tmp_path
    )
    try:
        result = subprocess.run(
            [sys.executable, str(guard), "web"], capture_output=True, text=True, check=False
        )
        assert result.returncode == 1
        assert str(process.pid) in result.stderr
        assert "do-not-print-this-secret" not in result.stderr + result.stdout
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_run_local_refuses_missing_units_instead_of_starting_detached_copy(tmp_path):
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = show ]; then echo not-found; exit 0; fi\n"
        "echo 'unexpected service operation' >&2\nexit 99\n"
    )
    systemctl.chmod(0o700)
    result = subprocess.run(
        ["bash", str(ROOT / "run-local.sh"), "start"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "Install the Valeria systemd units first" in result.stderr
    assert "unexpected service operation" not in result.stderr


def test_service_scripts_have_valid_shell_syntax():
    for script in ("run-local.sh", "deploy/run-service.sh", "deploy/install-systemd.sh"):
        subprocess.run(["bash", "-n", str(ROOT / script)], check=True)
