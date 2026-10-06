"""Plugin surfaces from the shell (PLUGINS.md, ApiVersion 1.2).

``blueferry send FILE… [--to PLUGIN[:TARGET]]`` is "Send to…": the targets
come from enabled plugins with the ``share`` capability, and a long transfer
continues in the plugin and shows up on its card. ``blueferry cards`` prints
the "From Plugins" section of the phone card and runs one of its actions.
"""
from __future__ import annotations

from collections.abc import Callable

import typer

from blueferry import companion_tools
from blueferry import plugin_surfaces as surfaces
from blueferry.plugin_api import CAPABILITY_CARD
from blueferry.plugin_api.surfaces import checked_share_paths
from blueferry.text_safety import terminal_text

_hooks: dict[str, Callable] = {
    "targets": surfaces.load_targets, "send": surfaces.send, "cards": surfaces.load_cards,
    "find": surfaces.find_plugin, "invoke": surfaces.invoke,
    "open_uri": lambda uri: companion_tools.default_system().open_uri(uri),
}


def _echo_targets(targets: surfaces.ShareTargets) -> None:
    for choice in targets.choices:
        label = surfaces.choice_label(choice, targets.choices)
        typer.echo(f"{choice.key}  {terminal_text(label)}")
    for problem in targets.problems:
        typer.echo(terminal_text(problem), err=True)


_FILES = typer.Argument(None, help="Files to send.", show_default=False)


def send(
    files: list[str] = _FILES,
    to: str = typer.Option(
        "", "--to", "-t", metavar="PLUGIN[:TARGET]",
        help="Plugin id or alias, optionally with a target; needed when there are several.",
    ),
    list_targets: bool = typer.Option(False, "--list", "-l", help="Only list the targets."),
) -> None:
    """Send files to a plugin target, e.g. the iPhone over LocalSend."""
    targets: surfaces.ShareTargets = _hooks["targets"]()
    if list_targets:
        if not targets.choices:
            typer.echo("No plugin offers “Send to”.", err=True)
        _echo_targets(targets)
        return
    if not files:
        typer.echo("Name at least one file (or use --list).", err=True)
        raise typer.Exit(code=2)
    try:
        paths = checked_share_paths(files)
    except ValueError as error:
        typer.echo(terminal_text(error), err=True)
        raise typer.Exit(code=2) from None
    try:
        choice = surfaces.resolve_choice(targets.choices, to or None)
    except LookupError as error:
        typer.echo(terminal_text(error), err=True)
        for problem in targets.problems:
            typer.echo(terminal_text(problem), err=True)
        raise typer.Exit(code=2) from None
    outcome: surfaces.Outcome = _hooks["send"](choice, paths)
    typer.echo(terminal_text(outcome.message), err=not outcome.ok)
    if not outcome.ok:
        raise typer.Exit(code=1)


def cards(
    run: str = typer.Option(
        "", "--run", metavar="PLUGIN:ITEM:ACTION",
        help="Run one action, e.g. io.example.calendar:next:open.",
    ),
) -> None:
    """Show the phone card's "From Plugins" section, or run one action."""
    if run:
        plugin_part, _sep, rest = run.rpartition(":")
        plugin_part, _sep2, item_id = plugin_part.rpartition(":")
        if not plugin_part or not item_id or not rest:
            typer.echo("Use --run PLUGIN:ITEM:ACTION.", err=True)
            raise typer.Exit(code=2)
        outcome: surfaces.Outcome = _hooks["invoke"](
            _hooks["find"](plugin_part, CAPABILITY_CARD), item_id, rest,
        )
        if outcome.message:
            typer.echo(terminal_text(outcome.message), err=not outcome.ok)
        if outcome.ok and outcome.open_uri:
            _hooks["open_uri"](outcome.open_uri)
        if not outcome.ok:
            raise typer.Exit(code=1)
        return
    loaded: list[surfaces.PluginCard] = _hooks["cards"]()
    if not loaded:
        typer.echo("No plugin adds anything to the phone card.")
    for card in loaded:
        typer.echo(terminal_text(card.name))
        if not card.ok:
            typer.echo(f"  {terminal_text(card.hint)}")
            continue
        if not card.items:
            typer.echo("  (nothing right now)")
        for item in card.items:
            line = terminal_text(item.title)
            if item.subtitle:
                line += f" \u2014 {terminal_text(item.subtitle)}"
            typer.echo(f"  {line}")
            for action in item.actions:
                typer.echo(f"    [{card.plugin_id}:{item.id}:{action.id}] "
                           f"{terminal_text(action.label)}")
