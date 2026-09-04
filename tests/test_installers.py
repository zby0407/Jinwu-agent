from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/zby0407/Jinwu-agent/archive/refs/heads/main.tar.gz"


def test_unix_installer_has_valid_shell_syntax() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable")

    subprocess.run(
        [shell, "-n", str(ROOT / "install.sh")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_powershell_installer_has_valid_syntax() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    env = os.environ.copy()
    env["JW_TEST_INSTALLER_PATH"] = str(ROOT / "install.ps1")
    subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            "[scriptblock]::Create([IO.File]::ReadAllText("
            "$env:JW_TEST_INSTALLER_PATH)) | Out-Null",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_unix_installer_invokes_uv_tool_install(tmp_path: Path) -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation_log = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$JW_TEST_UV_LOG"\n'
        "if [ \"$*\" = 'tool dir --bin' ]; then\n"
        "    printf '%s\\n' \"$JW_TEST_BIN_DIR\"\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "JW_INSTALL_SOURCE": SOURCE_URL,
            "JW_TEST_BIN_DIR": str(fake_bin),
            "JW_TEST_UV_LOG": str(invocation_log),
            "PATH": os.pathsep.join(
                [str(fake_bin), os.environ.get("PATH", "/usr/bin:/bin")]
            ),
        }
    )

    completed = subprocess.run(
        [shell, str(ROOT / "install.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert f"tool install --reinstall {SOURCE_URL}" in invocations
    assert "tool update-shell" in invocations
    assert "tool dir --bin" in invocations
    assert "installation complete" in completed.stdout


def test_readme_exposes_public_installers() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "https://raw.githubusercontent.com/zby0407/Jinwu-agent/main/install.sh"
        in readme
    )
    assert (
        "https://raw.githubusercontent.com/zby0407/Jinwu-agent/main/install.ps1"
        in readme
    )
