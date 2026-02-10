from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("git") is None,
    reason="gpg and git are required",
)
def test_gpg_wrapper_creates_signed_commit(tmp_path: Path) -> None:
    gnupg_home = tmp_path / "gnupg"
    gnupg_home.mkdir(mode=0o700)
    passphrase_file = tmp_path / "passphrase"
    passphrase_file.write_text("test-only-passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)
    environment = {
        **os.environ,
        "GNUPGHOME": str(gnupg_home),
        "GIT_GPG_PASSPHRASE_FILE": str(passphrase_file),
    }

    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(passphrase_file),
            "--quick-generate-key",
            "OpenCode Docker Test <docker-test@example.invalid>",
            "ed25519",
            "sign",
            "1d",
        ],
        check=True,
        capture_output=True,
        env=environment,
    )
    keys = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    fingerprint = next(
        line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:")
    )

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    for name, value in (
        ("user.name", "OpenCode Docker Test"),
        ("user.email", "docker-test@example.invalid"),
        ("gpg.format", "openpgp"),
        ("gpg.program", str(PROJECT_ROOT / "docker" / "git-gpg-sign")),
        ("user.signingKey", fingerprint),
        ("commit.gpgSign", "true"),
    ):
        subprocess.run(
            ["git", "-C", str(repository), "config", name, value],
            check=True,
        )
    (repository / "signed.txt").write_text("signed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "signed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "Signed commit"],
        check=True,
        env=environment,
    )

    commit = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "commit", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "gpgsig -----BEGIN PGP SIGNATURE-----" in commit
