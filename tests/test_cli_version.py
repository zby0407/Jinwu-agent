from __future__ import annotations

import pytest
import typer

from jw.cli import commands


def test_version_callback_uses_distribution_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requested_names: list[str] = []

    def fake_version(name: str) -> str:
        requested_names.append(name)
        return "1.2.3"

    monkeypatch.setattr(commands, "_pkg_version", fake_version)

    with pytest.raises(typer.Exit):
        commands._version_callback(True)

    assert requested_names == ["jw-agent"]
    assert capsys.readouterr().out.strip() == "JW 1.2.3"
