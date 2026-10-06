"""`blueferry plugins`: list plugins and forward to a plugin's own CLI.

``blueferry plugins list`` only parses manifests. ``blueferry plugins
<alias> ARGS…`` replaces this process with the plugin's ``Cli=`` command so
prompts (an API key without echo) work as usual. Bundled plugins that are
importable but not yet set up are reachable by alias too, so the very first
``setup`` works before any manifest exists.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable, Sequence

import typer

from blueferry import __version__
from blueferry.plugin_api.manifest import Discovery, discover
from blueferry.text_safety import terminal_text

# alias -> module of a plugin that ships in this source tree.
BUNDLED = {"immich": "blueferry_immich_photos"}


def _discover() -> Discovery:
    return discover(blueferry_version=__version__)


def _exec(argv: Sequence[str]) -> None:
    # argv comes from a vetted manifest (or the bundled table); no shell.
    os.execvp(argv[0], list(argv))  # nosec B606


def _bundled_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


_hooks: dict[str, Callable] = {
    "discover": _discover, "exec": _exec, "bundled": _bundled_available,
}


def forward(alias: str, args: Sequence[str]) -> None:
    plugin = _hooks["discover"]().find(alias)
    if plugin is not None and plugin.cli:
        _hooks["exec"]([*plugin.cli, *args])
        return
    module = BUNDLED.get(alias)
    if module is not None and _hooks["bundled"](module):
        _hooks["exec"]([sys.executable, "-m", module, *args])
        return
    typer.echo(
        f"No BlueFerry plugin called {terminal_text(alias)!r} is installed. "
        "See PLUGINS.md, or `blueferry plugins list`.",
        err=True,
    )
    raise typer.Exit(code=2)


def _list() -> None:
    found = _hooks["discover"]()
    if not found.plugins:
        typer.echo("No plugins installed.")
    for plugin in found.plugins:
        alias = f" ({plugin.alias})" if plugin.alias else ""
        typer.echo(
            f"{terminal_text(plugin.name)}{alias}  {plugin.id} {plugin.version}  "
            f"[{', '.join(plugin.capabilities)}]"
        )
    for name, reason in found.ignored:
        typer.echo(f"ignored {terminal_text(name)}: {terminal_text(reason)}", err=True)


def plugins(
    args: list[str] = typer.Argument(  # noqa: B008 - Typer's declaration style
        None, metavar="list | ALIAS [ARGS]...",
        help="`list` shows installed plugins; an alias runs that plugin's CLI.",
    ),
) -> None:
    """Out-of-process plugins: list them or run a plugin's own CLI."""
    if not args:
        typer.echo("Usage: blueferry plugins list | blueferry plugins ALIAS [ARGS]...")
        raise typer.Exit(code=2)
    if args[0] == "list":
        _list()
        return
    forward(args[0], args[1:])


PLUGINS_CONTEXT = {"allow_extra_args": True, "ignore_unknown_options": True}
