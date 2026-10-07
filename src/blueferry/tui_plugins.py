"""Plugin surfaces in the terminal client (PLUGINS.md, ApiVersion 1.2).

The phone screen (``o``) shows the "From Plugins" card section as an option
list: each item with its actions, Enter runs the highlighted one. ``s``
opens "Send to…": pick a target from a share plugin and type file paths.
An action that sends files itself (``send_to``, ApiVersion 1.4, such as
"Send files…" on a LocalSend device) opens the same dialog with only its
target.
Every plugin call runs in a worker thread with the core's timeouts; a plugin
that fails shows a dimmed line instead of its items.
"""
from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from blueferry import plugin_surfaces as surfaces
from blueferry import tui_design as design
from blueferry.plugin_api.surfaces import checked_share_paths
from blueferry.text_safety import terminal_text


def _plain(value: object) -> str:
    return terminal_text(value).replace("\n", " ")


def card_options(cards: list[surfaces.PluginCard] | None) -> list[Option]:
    """Items as disabled headings, their actions as selectable options.

    Option ids are ``action:<index>`` into :func:`card_actions`.
    """
    if cards is None:
        return [Option(design.empty("Loading…"), disabled=True)]
    options: list[Option] = []
    index = 0
    for card in cards:
        if len(cards) > 1 or not card.ok:
            options.append(Option(Text(_plain(card.name), style="dim"), disabled=True))
        if not card.ok:
            options.append(Option(design.empty(f"  {_plain(card.hint)}"), disabled=True))
            continue
        for item in card.items:
            line = Text(f"  {_plain(item.title)}", style="bold")
            if item.subtitle:
                line.append(f"  {_plain(item.subtitle)}", style="dim")
            options.append(Option(line, disabled=not item.actions))
            for action in item.actions:
                marker = "▸" if action.kind == "primary" else " "
                options.append(Option(f"    {marker} {_plain(action.label)}", id=f"action:{index}"))
                index += 1
    return options


def card_actions(cards: list[surfaces.PluginCard] | None) -> list[tuple[str, str, str]]:
    """(plugin id, item id, action id) in the order of :func:`card_options`."""
    return [
        (card.plugin_id, item.id, action.id)
        for card in cards or [] if card.ok
        for item in card.items
        for action in item.actions
    ]


def card_sends(cards: list[surfaces.PluginCard] | None) -> list[tuple[str, str]]:
    """(share target, item title) per entry of :func:`card_actions`; the
    target is ``""`` for actions that do not send files."""
    return [
        (surfaces.send_target(card, action), item.title)
        for card in cards or [] if card.ok
        for item in card.items
        for action in item.actions
    ]


class SendToScreen(ModalScreen[None]):
    """Pick a share target, type paths, Enter sends."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(
        self,
        *,
        load: Callable[[], surfaces.ShareTargets] = surfaces.load_targets,
        send: Callable[..., surfaces.Outcome] = surfaces.send,
        on_sent: Callable[[], None] | None = None,
        choice: surfaces.ShareChoice | None = None,
    ) -> None:
        super().__init__()
        # A card action that sends files names its one target: no plugin call.
        self._load = load if choice is None else (lambda: surfaces.ShareTargets([choice]))
        self._title = "Send to…" if choice is None else f"Send to {_plain(choice.label)}"
        self._send = send
        self._on_sent = on_sent
        self._choices: list[surfaces.ShareChoice] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="send-dialog", classes="dialog"):
            yield Static(self._title, classes="dialog-title", markup=False)
            yield Static(design.section("Target"), classes="section-title")
            yield OptionList(Option(design.empty("Looking for targets…"), disabled=True),
                             id="send-targets")
            yield Static(design.section("Files"), classes="section-title")
            yield Input(placeholder="~/Pictures/a.jpg \"/path with spaces/b.pdf\"",
                        id="send-paths")
            yield Static(design.key_hints(
                ("↑↓", "target"), ("Enter", "send"), ("Esc", "close"),
            ), classes="key-hints")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="send-close")
                yield Button("Send", variant="primary", id="send-go")

    def on_mount(self) -> None:
        self.load_targets()

    @work(thread=True, exclusive=True, group="send-targets", exit_on_error=False)
    def load_targets(self) -> None:
        targets = self._load()
        self.app.call_from_thread(self._show_targets, targets)

    def _show_targets(self, targets: surfaces.ShareTargets) -> None:
        self._choices = list(targets.choices)
        options = self.query_one("#send-targets", OptionList)
        options.clear_options()
        for choice in self._choices:
            options.add_option(Option(_plain(surfaces.choice_label(choice, self._choices))))
        if not self._choices:
            options.add_option(Option(design.empty(
                "No plugin offers “Send to”. Add one under Settings > Plugins."),
                disabled=True))
        for problem in targets.problems:
            options.add_option(Option(design.empty(_plain(problem)), disabled=True))
        if self._choices:
            options.highlighted = 0
            self.query_one("#send-paths", Input).focus()

    @on(Button.Pressed, "#send-go")
    @on(Input.Submitted, "#send-paths")
    def send(self) -> None:
        index = self.query_one("#send-targets", OptionList).highlighted
        if index is None or not 0 <= index < len(self._choices):
            self.notify("Choose a target first.", severity="warning")
            return
        try:
            paths = checked_share_paths(shlex.split(self.query_one("#send-paths", Input).value))
        except ValueError as error:
            self.notify(_plain(error), severity="warning", markup=False)
            return
        self._sending(self._choices[index], paths)

    @work(thread=True, group="send-files", exit_on_error=False)
    def _sending(self, choice: surfaces.ShareChoice, paths: list[str]) -> None:
        outcome = self._send(choice, paths)
        self.app.call_from_thread(self._sent, outcome)

    def _sent(self, outcome: surfaces.Outcome) -> None:
        self.notify(_plain(outcome.message), severity="information" if outcome.ok else "error",
                    markup=False)
        if outcome.ok:
            if self._on_sent is not None:
                self._on_sent()
            self.dismiss(None)

    @on(Button.Pressed, "#send-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
