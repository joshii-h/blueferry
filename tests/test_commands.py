"""External command boundary behavior."""
from __future__ import annotations

import subprocess
import sys

import pytest

from blueferry import commands
from blueferry.errors import CommandError


def test_nonzero_exit_uses_stderr_as_the_actionable_error(monkeypatch):
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["systemctl"], 1, stdout="", stderr="permission denied\n"
        ),
    )

    with pytest.raises(CommandError, match="permission denied") as caught:
        commands.run_command(["systemctl", "restart", "bluetooth"], timeout=10)

    assert caught.value.returncode == 1
    assert caught.value.argv == ("systemctl", "restart", "bluetooth")


def test_check_false_returns_nonzero_result(monkeypatch):
    expected = subprocess.CompletedProcess(["probe"], 3, "details", "")
    monkeypatch.setattr(commands.subprocess, "run", lambda *_args, **_kwargs: expected)

    assert commands.run_command(["probe"], timeout=2, check=False) is expected


def test_spawn_command_feeds_stdin_to_an_inert_helper(tmp_path) -> None:
    output = tmp_path / "received"
    script = f"import sys; open({str(output)!r}, 'w').write(sys.stdin.read())"

    process = commands.spawn_command([sys.executable, "-c", script], stdin_text="482913")

    assert process.wait(timeout=10) == 0
    assert output.read_text() == "482913"


def test_spawn_command_requires_an_absolute_executable() -> None:
    with pytest.raises(ValueError):
        commands.spawn_command(["wl-copy"], stdin_text="482913")


def test_spawn_command_normalizes_a_missing_executable(tmp_path) -> None:
    with pytest.raises(CommandError):
        commands.spawn_command([str(tmp_path / "missing")], stdin_text="482913")
