"""`blueferry plugins`: discovery listing and forwarding; never execs."""
from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from blueferry import cli_plugins
from blueferry.cli import app
from blueferry.plugin_api.manifest import Discovery
from blueferry.plugin_api.testing import manifest


@pytest.fixture
def hooks(monkeypatch):
    executed: list[list[str]] = []
    state = {"discovery": Discovery(), "bundled": False}
    monkeypatch.setitem(cli_plugins._hooks, "discover", lambda: state["discovery"])
    monkeypatch.setitem(cli_plugins._hooks, "exec", lambda argv: executed.append(list(argv)))
    monkeypatch.setitem(cli_plugins._hooks, "bundled", lambda _module: state["bundled"])
    return state, executed


def test_alias_forwards_every_argument_to_the_plugin_cli(hooks) -> None:
    state, executed = hooks
    state["discovery"] = Discovery((manifest(extra="Cli=immich-cli --x\nAlias=immich\n"),))
    result = CliRunner().invoke(
        app, ["plugins", "immich", "setup", "--url", "https://photos.example.org"],
    )
    assert result.exit_code == 0, result.output
    assert executed == [["immich-cli", "--x", "setup", "--url", "https://photos.example.org"]]


def test_bundled_plugin_is_reachable_before_its_first_setup(hooks) -> None:
    state, executed = hooks
    state["bundled"] = True
    result = CliRunner().invoke(app, ["plugins", "immich", "setup"])
    assert result.exit_code == 0
    assert executed == [[sys.executable, "-m", "blueferry_immich_photos", "setup"]]


def test_unknown_alias_and_listing(hooks) -> None:
    state, executed = hooks
    result = CliRunner().invoke(app, ["plugins", "nope"])
    assert result.exit_code == 2 and "No BlueFerry plugin" in result.output
    state["discovery"] = Discovery(
        (manifest(extra="Alias=immich\n"),), (("bad.plugin", "written for plugin API 9"),),
    )
    result = CliRunner().invoke(app, ["plugins", "list"])
    assert "Example (immich)  io.example.photos 1.0  [photos]" in result.output
    assert "ignored bad.plugin: written for plugin API 9" in result.output
    assert executed == []
