"""`blueferry plugins`: manage plugins and forward to a plugin's own CLI.

``list``, ``install``, ``update``, ``remove``, ``enable``, ``disable``,
``config``, ``available``/``search`` and ``index`` manage plugins (see
:mod:`blueferry.plugin_manager` and :mod:`blueferry.plugin_index`).
Anything else is an alias: ``blueferry plugins <alias> ARGS…`` replaces this
process with the plugin's ``Cli=`` command so prompts (an API key without
echo) work as usual.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from collections.abc import Callable, Sequence

import typer

from blueferry import __version__
from blueferry.plugin_api import SUPPORTED_API_VERSIONS
from blueferry.plugin_api.client import PluginClient, PluginError
from blueferry.plugin_api.config import SECRET_MASK
from blueferry.plugin_api.manifest import Discovery, discover
from blueferry.plugin_index import (
    DEFAULT_INDEX_URL,
    IndexFetchError,
    PluginIndex,
    check_index_url,
    index_urls,
    search,
)
from blueferry.plugin_manager import InstallError, PluginManager, PreparedInstall
from blueferry.text_safety import terminal_text

COMMANDS = frozenset({
    "list", "install", "update", "remove", "enable", "disable", "config",
    "available", "search", "index",
})


def _discover() -> Discovery:
    return discover(blueferry_version=__version__)


def _exec(argv: Sequence[str]) -> None:
    # argv comes from a vetted manifest; no shell.
    os.execvp(argv[0], list(argv))  # nosec B606


_hooks: dict[str, Callable] = {
    "discover": _discover,
    "exec": _exec,
    "manager": PluginManager,
    "index": PluginIndex,
    "client": PluginClient,
    "confirm": lambda text: typer.confirm(text, default=False),
    "secret": lambda prompt: getpass.getpass(prompt),
}


def _echo(text: object = "", *, err: bool = False) -> None:
    typer.echo(terminal_text(text), err=err)


def _fail(message: object, code: int = 1) -> None:
    _echo(message, err=True)
    raise typer.Exit(code=code)


def forward(alias: str, args: Sequence[str]) -> None:
    plugin = _hooks["discover"]().find(alias)
    if plugin is not None and plugin.cli:
        _hooks["exec"]([*plugin.cli, *args])
        return
    typer.echo(
        f"No BlueFerry plugin called {terminal_text(alias)!r} is installed. "
        "See `blueferry plugins available` or `blueferry plugins list`.",
        err=True,
    )
    raise typer.Exit(code=2)


def _list(manager: PluginManager) -> None:
    entries, ignored = manager.entries()
    if not entries:
        typer.echo("No plugins installed.")
    for entry in entries:
        plugin = entry.manifest
        alias = f" ({plugin.alias})" if plugin.alias else ""
        state = "" if entry.enabled else "  [disabled]"
        _echo(f"{plugin.name}{alias}  {plugin.id} {plugin.version}  "
              f"[{', '.join(plugin.capabilities)}]{state}")
        if entry.record is not None:
            _echo(f"    from {entry.record.url} at {entry.record.ref_label}")
        elif entry.source:
            _echo(f"    source {entry.source} (not managed by `plugins install`)")
    for name, reason in ignored:
        _echo(f"ignored {name}: {reason}", err=True)


def _show(prepared: PreparedInstall) -> None:
    for label, value in prepared.summary():
        _echo(f"  {label + ':':<14}{value}")
    if prepared.changes:
        _echo("  Changes:")
        for line in prepared.changes:
            _echo(f"    {line}")


def _finish(manager: PluginManager, prepared: PreparedInstall, yes: bool, verb: str) -> None:
    _show(prepared)
    if not yes and not _hooks["confirm"](f"{verb} this plugin?"):
        manager.discard(prepared)
        _fail("Cancelled.", 1)
    _echo("Creating the plugin's environment; this can take a minute…")
    try:
        record = manager.commit(prepared)
    except InstallError as error:
        _fail(f"Failed: {error}")
        return
    done = "Updated" if verb == "update" else "Installed"
    _echo(f"{done} {prepared.manifest.name} at {record.ref_label}.")
    if prepared.manifest.config:
        _echo(f"Configure it in BlueFerry's settings or with: "
              f"blueferry plugins config {prepared.manifest.id}")


def _install(manager: PluginManager, url: str, ref: str | None, yes: bool) -> None:
    _echo(f"Fetching {url}…")
    try:
        prepared = manager.prepare(url, ref)
    except InstallError as error:
        _fail(error)
        return
    _finish(manager, prepared, yes, "install")


def _update(manager: PluginManager, plugin_id: str, yes: bool) -> None:
    try:
        record, ref, commit = manager.check_update(plugin_id)
        if commit == record.commit:
            _echo(f"{plugin_id} is up to date ({record.ref_label}).")
            return
        _echo(f"Update: {record.ref_label} ({record.commit[:12]}) -> "
              f"{ref if ref != commit else commit[:12]} ({commit[:12]})")
        prepared = manager.prepare_update(plugin_id)
    except InstallError as error:
        _fail(error)
        return
    if prepared is None:
        _echo(f"{plugin_id} is up to date.")
        return
    _finish(manager, prepared, yes, "update")


def _remove(manager: PluginManager, plugin_id: str, yes: bool) -> None:
    if plugin_id not in manager.records():
        _fail(f"{plugin_id} was not installed with `blueferry plugins install`.")
    if not yes and not _hooks["confirm"](
        f"Remove {plugin_id}? Its own settings and keyring entries stay."
    ):
        _fail("Cancelled.")
    try:
        removed = manager.remove(plugin_id)
    except InstallError as error:
        _fail(error)
        return
    for path in removed:
        _echo(f"Removed {path}")


def _config(manager: PluginManager, plugin_id: str, assignments: Sequence[str],
            secrets: Sequence[str]) -> None:
    plugin = _hooks["discover"]().find(plugin_id)
    if plugin is None:
        _fail(f"No plugin {plugin_id} is installed.", 2)
        return
    if not plugin.config:
        _fail(f"{plugin.name} has no settings.", 2)
    client = _hooks["client"](plugin)
    fields = {field.key: field for field in plugin.config}
    update: dict[str, object] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        field = fields.get(key.strip())
        if not separator or field is None or field.secret:
            _fail(f"--set needs KEY=VALUE with a non-secret key of: {', '.join(fields)}", 2)
            return
        value: object = raw
        if field.type == "bool":
            value = raw.strip().casefold() in {"1", "true", "yes", "on"}
        elif field.type == "int":
            try:
                value = int(raw)
            except ValueError:
                _fail(f"{key} must be a whole number", 2)
        update[field.key] = value
    for key in secrets:
        field = fields.get(key)
        if field is None or not field.secret:
            _fail(f"--secret needs a secret key of: "
                  f"{', '.join(f.key for f in plugin.config if f.secret)}", 2)
            return
        update[key] = _hooks["secret"](f"{field.label} (input hidden): ").strip()
    try:
        if update:
            result = client.set_config(update)
            if not result.ok:
                for key, reason in result.errors.items():
                    _echo(f"{key or 'settings'}: {reason}", err=True)
                raise typer.Exit(code=1)
            _echo("Saved.")
        values = client.get_config()
    except PluginError as error:
        _fail(error)
        return
    for field in plugin.config:
        shown = values.get(field.key)
        if field.secret:
            shown = "(stored)" if shown == SECRET_MASK else "(not set)"
        _echo(f"{field.key} = {json.dumps(shown) if not field.secret else shown}"
              f"  # {field.label}{' (required)' if field.required else ''}")


def _available(manager: PluginManager, term: str, refresh: bool) -> None:
    entries, _ignored = manager.entries()
    catalog = _hooks["index"]().catalog(
        index_urls(manager.settings()),
        installed={entry.manifest.id: entry.manifest.version for entry in entries},
        records=manager.records(),
        blueferry_version=__version__,
        supported_api=SUPPORTED_API_VERSIONS,
        refresh=refresh,
    )
    for url, problem in catalog.problems:
        _echo(f"{url}: {problem}", err=True)
    items = search(catalog.items, term)
    if not items:
        typer.echo("No plugins found.")
    for item in items:
        entry = item.entry
        if item.update_available:
            state = f"update available ({item.installed_ref} -> {entry.ref})"
        elif item.installed:
            state = f"installed {item.installed_ref}".rstrip()
        elif not entry.available:
            state = "coming soon"
        elif not item.compatible:
            state = "needs a newer BlueFerry"
        else:
            state = f"available {entry.ref}"
        _echo(f"{entry.emoji + ' ' if entry.emoji else ''}{entry.name}  [{state}]  "
              f"{', '.join(entry.capabilities)}")
        if entry.description:
            _echo(f"    {entry.description}")
        if entry.available and not item.installed:
            _echo(f"    blueferry plugins install {entry.repo} --ref {entry.ref}")


def _index(manager: PluginManager, action: str, url: str | None) -> None:
    urls = index_urls(manager.settings())
    if action in ("add", "remove"):
        if not url:
            _fail(f"index {action} needs a URL", 2)
            return
        try:
            url = check_index_url(url)
        except InstallError as error:
            _fail(error, 2)
            return
        urls = [*urls, url] if action == "add" else [u for u in urls if u != url]
        manager.update_settings(indexes=list(dict.fromkeys(urls)))
    elif action == "reset":
        manager.update_settings(indexes=[DEFAULT_INDEX_URL])
    for current in index_urls(manager.settings()):
        _echo(current)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blueferry plugins")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="installed plugins")
    install = commands.add_parser("install", help="install a plugin from an https Git URL")
    install.add_argument("url")
    install.add_argument("--ref", help="tag or full commit (default: newest version tag)")
    install.add_argument("--yes", "-y", action="store_true", help="do not ask")
    for name in ("update", "remove"):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument("--yes", "-y", action="store_true", help="do not ask")
    for name in ("enable", "disable"):
        commands.add_parser(name).add_argument("id")
    config = commands.add_parser("config", help="show or change a plugin's settings")
    config.add_argument("id")
    config.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    config.add_argument("--secret", action="append", default=[], metavar="KEY",
                        help="prompt for a secret without echo")
    for name in ("available", "search"):
        command = commands.add_parser(name, help="plugins offered by the plugin indexes")
        command.add_argument("term", nargs="?" if name == "available" else None, default="")
        command.add_argument("--refresh", action="store_true", help="ignore the cache")
    index = commands.add_parser("index", help="list, add or remove plugin index URLs")
    index.add_argument("action", nargs="?", default="list",
                       choices=["list", "add", "remove", "reset"])
    index.add_argument("url", nargs="?")
    return parser


def manage(args: Sequence[str]) -> None:
    try:
        options = _parser().parse_args(list(args))
    except SystemExit as error:
        raise typer.Exit(code=int(error.code or 0)) from None
    manager = _hooks["manager"]()
    command = options.command
    try:
        if command == "list":
            _list(manager)
        elif command == "install":
            _install(manager, options.url, options.ref, options.yes)
        elif command == "update":
            _update(manager, options.id, options.yes)
        elif command == "remove":
            _remove(manager, options.id, options.yes)
        elif command in ("enable", "disable"):
            manager.set_enabled(options.id, command == "enable")
            _echo(f"{options.id} {command}d.")
        elif command == "config":
            _config(manager, options.id, options.set, options.secret)
        elif command in ("available", "search"):
            _available(manager, options.term or "", options.refresh)
        elif command == "index":
            _index(manager, options.action, options.url)
    except (InstallError, IndexFetchError, OSError) as error:
        _fail(error)


def plugins(
    args: list[str] = typer.Argument(  # noqa: B008 - Typer's declaration style
        None, metavar="COMMAND | ALIAS [ARGS]...",
        help="list, install URL, update ID, remove ID, enable/disable ID, config ID, "
             "available, search TERM, index; an alias runs that plugin's CLI.",
    ),
) -> None:
    """Manage out-of-process plugins or run a plugin's own CLI."""
    if not args:
        typer.echo("Usage: blueferry plugins list | install URL [--ref REF] [--yes] | "
                   "update ID | remove ID | enable ID | disable ID | config ID | "
                   "available | search TERM | index | ALIAS [ARGS]...")
        raise typer.Exit(code=2)
    if args[0] in COMMANDS or args[0] in ("-h", "--help"):
        manage(args)
        return
    forward(args[0], args[1:])


PLUGINS_CONTEXT = {
    "allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": [],
}
