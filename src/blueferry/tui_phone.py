"""Phone overview for the Textual client: status strip text and a modal screen.

The strip under the service pills shows battery, signal, network and now
playing. The modal (``o``) lists the switches, the companion tools (UxPlay,
LocalSend, iPhone photos over USB), recent calls and the iPhone
app notifications. Every blocking backend call runs in a worker thread, and
every iPhone-supplied string is rendered through ``rich.text.Text`` after
``terminal_text`` so it can carry neither markup nor terminal controls.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar, Protocol

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static

from blueferry import companion_tools, tui_plugins
from blueferry import phone_overview as overview
from blueferry import plugin_surfaces as surfaces
from blueferry import tui_design as design
from blueferry.client import BackendError
from blueferry.models import BackendStatus, CallHistoryEntry, phone_status_fields
from blueferry.reconnect_view import reconnect_view, result_text
from blueferry.tether_status import TetherStatus
from blueferry.text_safety import terminal_text

_MAX_ROWS = 50
# Keys of the companion tools in the overview screen, by action.
RECONNECT_KEY = "b"
TOOL_KEYS = {"mirror": "m", "send": "f", "photos": "i", "eject": "e", "pair": "y"}


class PhoneClient(Protocol):
    def call_history(self, limit: int = 200) -> list[CallHistoryEntry]: ...

    def notifications(self, limit: int = 50) -> dict: ...

    def set_proximity_lock(self, enabled: bool, grace_seconds: int) -> dict: ...

    def set_mirror_notification_removals(self, enabled: bool) -> bool: ...

    def reconnect_phone(self) -> str: ...


def _plain(value: object) -> str:
    return terminal_text(value).replace("\n", " ")


def phone_text(status: BackendStatus) -> Text:
    """Battery, signal and network, or why they are missing."""
    fields = phone_status_fields(status)
    if fields:
        return Text("  ·  ".join(f"{label} {_plain(value)}" for label, value in fields))
    return Text(overview.phone_status_hint(status.to_dict()), style="dim")


def media_text(view: Mapping[str, Any]) -> Text:
    if not view.get("available"):
        return Text(str(view.get("hint") or ""), style="dim")
    icon = "▶" if view.get("playing") else "⏸"
    return Text(f"{icon}  {_plain(view.get('title'))}   [ p  [ ] ]")


def mirror_switch(status: Mapping[str, Any]) -> tuple[bool, bool, str]:
    """(on, available, hint) of "Sync notifications with iPhone"."""
    value = status.get("mirror_iphone_removals")
    if value is None:
        return False, False, "Not offered by the running BlueFerry service."
    if value is True:
        return True, True, "Notifications removed on the iPhone also disappear here."
    return False, True, "The list keeps notifications removed on the iPhone."


def switches_text(
    status: Mapping[str, Any], tether: TetherStatus | None,
) -> Text:
    audio = overview.phone_audio(status)
    hotspot = overview.tether(tether)
    lock = overview.proximity(status)
    lines = [
        ("a", "Sound on this computer", audio["on_pc"], audio["available"], audio["hint"]),
        ("t", "Personal Hotspot", hotspot["active"], hotspot["available"], hotspot["hint"]),
        ("l", "Lock when the iPhone goes away", lock["enabled"], lock["available"], lock["hint"]),
        ("x", "Sync notifications with iPhone", *mirror_switch(status)),
    ]
    text = Text()
    for key, label, on_, usable, hint in lines:
        state = "ON " if on_ else "OFF"
        text.append(f"[{key}] {state}  {label}\n", style="" if usable else "dim")
        text.append(f"      {_plain(hint)}\n", style="dim")
    reconnect = reconnect_view(status)
    if reconnect.available:
        text.append(
            f"[{RECONNECT_KEY}]      Reconnect iPhone\n",
            style="" if reconnect.offered else "dim",
        )
        text.append(f"      {_plain(reconnect.hint)}\n", style="dim")
    return text


def tools_text(
    snapshot: companion_tools.Snapshot | None, *, busy: str = "", needs_pairing: bool = False,
) -> Text:
    """Screen mirroring, LocalSend and iPhone photos with their keys.

    Missing tools stay listed, dimmed, with what to install; eject and the
    trust request only show when they apply.
    """
    if snapshot is None:
        return Text("Looking for tools…", style="dim")
    text = Text()
    for tool in snapshot.tools:
        if tool.key == "eject" and not tool.enabled:
            continue
        state = "…  " if busy == tool.key else ("ON " if tool.active else "   ")
        style = "" if tool.enabled and not busy else "dim"
        text.append(f"[{TOOL_KEYS[tool.key]}] {state} {_plain(tool.title)}\n", style=style)
        text.append(f"      {_plain(tool.subtitle)}\n", style="dim")
    if needs_pairing:
        text.append(
            f"[{TOOL_KEYS['pair']}]      Ask the iPhone to trust this computer\n",
            style="" if not busy else "dim",
        )
    return text


def calls_text(status: Mapping[str, Any], entries: list[CallHistoryEntry] | None) -> Text:
    hint = overview.call_history_hint(status)
    if hint:
        return Text(hint, style="dim")
    if entries is None:
        return Text("Loading…", style="dim")
    rows = overview.call_groups(entries[:_MAX_ROWS])
    if not rows:
        return design.empty("No recent calls")
    text = Text()
    day = None
    for row in rows:
        if row["day"] != day:
            day = row["day"]
            text.append(f"{_plain(day)}\n", style=f"bold {design.ACCENT}")
        count = f" ({row['count']})" if row["count"] > 1 else ""
        text.append(
            f"  {row['clock']:>8}  {row['direction']:<9} {_plain(row['caller'])}{count}\n",
            style=f"bold {design.MISSED}" if row["missed"] else "",
        )
    text.append("Press c to call back from the list.", style="dim")
    return text


def notifications_text(status: Mapping[str, Any], snapshot: object) -> Text:
    hint = overview.notifications_hint(status)
    if hint:
        return Text(hint, style="dim")
    if snapshot is None:
        return Text("Loading…", style="dim")
    records = snapshot.get("notifications") if isinstance(snapshot, Mapping) else None
    rows = overview.notification_rows(records)[:_MAX_ROWS]
    if not rows:
        return design.empty("No notifications yet")
    text = Text()
    for row in rows:
        text.append(f"{_plain(row['app'])}  {_plain(row['time'])}\n", style="bold")
        body = " — ".join(_plain(row[key]) for key in ("title", "body") if row[key])
        if body:
            text.append(f"  {body}\n")
    return text


class PhoneScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,o", "close", "Close", show=False),
        Binding("l", "toggle_lock", "Lock when away", show=False),
        Binding("x", "toggle_mirror", "Sync notifications", show=False),
        Binding(RECONNECT_KEY, "reconnect", "Reconnect iPhone", show=False),
        Binding("s", "send_to", "Send to…", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        *[
            Binding(key, f"tool('{action}')", show=False)
            for action, key in TOOL_KEYS.items()
        ],
    ]

    def __init__(
        self,
        client: PhoneClient,
        status: Callable[[], BackendStatus],
        tether: Callable[[], TetherStatus | None],
        *,
        tools_system: companion_tools.System | None = None,
        load_cards: Callable[[], list[surfaces.PluginCard]] = surfaces.load_cards,
        invoke: Callable[..., surfaces.Outcome] | None = None,
        send_screen: Callable[..., ModalScreen] = tui_plugins.SendToScreen,
    ) -> None:
        super().__init__()
        self._load_cards = load_cards
        self._invoke = invoke or (lambda plugin_id, item_id, action_id: surfaces.invoke(
            surfaces.find_plugin(plugin_id, "card"), item_id, action_id))
        self._send_screen = send_screen
        self._cards: list[surfaces.PluginCard] | None = None
        self._tools_system = tools_system or companion_tools.default_system()
        self._tools: companion_tools.Snapshot | None = None
        self._tool_busy = ""
        self._needs_pairing = False
        self._client = client
        self._status = status
        self._tether = tether
        self._calls: list[CallHistoryEntry] | None = None
        self._notifications: object = None

    def compose(self) -> ComposeResult:
        with Vertical(id="phone-dialog", classes="dialog"):
            yield Static("Phone", classes="dialog-title")
            with VerticalScroll(id="phone-body"):
                yield Static(design.section(design.QUICK_SETTINGS), classes="section-title")
                yield Static("", id="phone-switches", classes="dialog-copy")
                yield Static(design.section(design.FROM_PLUGINS), id="phone-plugins-title",
                             classes="section-title")
                yield OptionList(id="phone-plugins")
                yield Static(design.section(design.TOOLS), classes="section-title")
                yield Static("", id="phone-tools", classes="dialog-copy")
                yield Static(design.section(design.RECENT_CALLS), classes="section-title")
                yield Static("", id="phone-calls", classes="dialog-copy")
                yield Static(design.section(design.NOTIFICATIONS), classes="section-title")
                yield Static("", id="phone-notifications", classes="dialog-copy")
            yield Static(design.key_hints(
                ("Enter", "plugin action"), ("s", "send to"), ("r", "refresh"),
                ("letters", "switch or start"), ("Esc", "close"),
            ), classes="key-hints")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="phone-close")

    def on_mount(self) -> None:
        self.render_phone()
        self.reload()
        self.probe_tools()
        self.load_plugins()

    # ---- From Plugins (capability card) and Send to… (share) ----------------

    @work(thread=True, exclusive=True, group="phone-plugins", exit_on_error=False)
    def load_plugins(self) -> None:
        try:
            cards = self._load_cards()
        except Exception as error:  # a plugin must never end the terminal client
            cards = [surfaces.PluginCard("", "Plugins", False, _plain(type(error).__name__))]
        self.app.call_from_thread(self._plugins_loaded, cards)

    def _plugins_loaded(self, cards: list[surfaces.PluginCard]) -> None:
        self._cards = cards
        if self.is_attached:
            self.render_plugins()

    def render_plugins(self) -> None:
        options = self.query_one("#phone-plugins", OptionList)
        shown = self._cards is None or bool(self._cards)
        options.display = shown
        self.query_one("#phone-plugins-title", Static).display = shown
        options.clear_options()
        options.add_options(tui_plugins.card_options(self._cards))

    def action_refresh(self) -> None:
        """r: everything on this screen, and the app's status like before."""
        refresh = getattr(self.app, "action_refresh", None)
        if refresh is not None:
            refresh()
        self.reload()
        self.probe_tools()
        self.load_plugins()

    @on(OptionList.OptionSelected, "#phone-plugins")
    def plugin_action(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        actions = tui_plugins.card_actions(self._cards)
        if not option_id.startswith("action:"):
            return
        index = int(option_id[7:])
        if not 0 <= index < len(actions):
            return
        target, label = tui_plugins.card_sends(self._cards)[index]
        if target:
            self._open_card_send(actions[index][0], target, label)
        else:
            self._run_plugin_action(*actions[index])

    @work(thread=True, group="phone-plugin-action", exit_on_error=False)
    def _open_card_send(self, plugin_id: str, target: str, label: str) -> None:
        """A card action with send_to: ask for paths, send to that target."""
        choice = surfaces.card_choice(plugin_id, target, label)  # reads manifests
        self.app.call_from_thread(self._show_card_send, choice)

    def _show_card_send(self, choice: surfaces.ShareChoice | None) -> None:
        if choice is None:
            self.notify("The plugin can no longer send files.", severity="warning")
            return
        self.app.push_screen(self._send_screen(on_sent=self.load_plugins, choice=choice))

    @work(thread=True, group="phone-plugin-action", exit_on_error=False)
    def _run_plugin_action(self, plugin_id: str, item_id: str, action_id: str) -> None:
        outcome = self._invoke(plugin_id, item_id, action_id)
        self.app.call_from_thread(self._plugin_action_done, outcome)

    def _plugin_action_done(self, outcome: surfaces.Outcome) -> None:
        if outcome.message:
            self.notify(_plain(outcome.message),
                        severity="information" if outcome.ok else "warning", markup=False)
        if outcome.ok and outcome.open_uri:
            try:
                self._tools_system.open_uri(outcome.open_uri)
            except Exception as error:  # no viewer must not end the TUI
                self.notify(f"Could not open: {_plain(error)}", severity="error", markup=False)
        self.load_plugins()

    def action_send_to(self) -> None:
        self.app.push_screen(self._send_screen(on_sent=self.load_plugins))

    def render_phone(self) -> None:
        try:
            switches = self.query_one("#phone-switches", Static)
        except NoMatches:
            # A status snapshot can arrive after the screen is pushed but
            # before it is composed. on_mount renders it once it is.
            return
        status = self._status().to_dict()
        switches.update(switches_text(status, self._tether()))
        self.query_one("#phone-tools", Static).update(tools_text(
            self._tools, busy=self._tool_busy, needs_pairing=self._needs_pairing,
        ))
        self.query_one("#phone-calls", Static).update(calls_text(status, self._calls))
        self.query_one("#phone-notifications", Static).update(
            notifications_text(status, self._notifications)
        )

    @work(thread=True, exclusive=True, group="phone-lists", exit_on_error=False)
    def reload(self) -> None:
        status = self._status()
        calls: list[CallHistoryEntry] | None = []
        notifications: object = {}
        try:
            if status.extra.get("call_history_enabled") is True:
                calls = self._client.call_history(_MAX_ROWS)
            if status.extra.get("notification_history_enabled") is True:
                notifications = self._client.notifications(_MAX_ROWS)
        except BackendError as error:
            self.app.call_from_thread(
                self.notify, _plain(error), severity="error", markup=False,
            )
        self.app.call_from_thread(self._loaded, calls, notifications)

    def _loaded(self, calls: list[CallHistoryEntry] | None, notifications: object) -> None:
        self._calls, self._notifications = calls, notifications
        if self.is_attached:
            self.render_phone()

    def action_toggle_lock(self) -> None:
        lock = overview.proximity(self._status().to_dict())
        if not lock["available"]:
            self.notify(lock["hint"], severity="warning")
            return
        self._set_lock(not lock["enabled"], int(lock["grace"]))

    def action_toggle_mirror(self) -> None:
        enabled, available, hint = mirror_switch(self._status().to_dict())
        if not available:
            self.notify(hint, severity="warning")
            return
        self._set_mirror(not enabled)

    @work(thread=True, group="phone-action", exit_on_error=False)
    def _set_mirror(self, enabled: bool) -> None:
        try:
            self._client.set_mirror_notification_removals(enabled)
        except BackendError as error:
            self.app.call_from_thread(
                self.notify, _plain(error), severity="error", markup=False,
            )
            return
        self.app.call_from_thread(
            self.notify,
            "Notifications follow the iPhone" if enabled else "Notifications kept as history",
        )
        refresh = getattr(self.app, "action_refresh", None)
        if refresh is not None:
            self.app.call_from_thread(refresh)

    def action_reconnect(self) -> None:
        view = reconnect_view(self._status().to_dict())
        if not view.offered:
            self.notify(view.hint, severity="information")
            return
        self._reconnect()

    @work(thread=True, group="phone-action", exit_on_error=False)
    def _reconnect(self) -> None:
        try:
            result = self._client.reconnect_phone()
        except BackendError as error:
            self.app.call_from_thread(
                self.notify, _plain(error), severity="error", markup=False,
            )
            return
        self.app.call_from_thread(
            self.notify, result_text(result),
            severity="error"
            if result in ("unreachable", "bluez-kernel", "bluez-unresponsive")
            else "information",
            markup=False,
        )
        refresh = getattr(self.app, "action_refresh", None)
        if refresh is not None:
            self.app.call_from_thread(refresh)

    @work(thread=True, group="phone-action", exit_on_error=False)
    def _set_lock(self, enabled: bool, grace: int) -> None:
        try:
            self._client.set_proximity_lock(enabled, grace)
        except BackendError as error:
            self.app.call_from_thread(
                self.notify, _plain(error), severity="error", markup=False,
            )
            return
        self.app.call_from_thread(
            self.notify, "Lock when away on" if enabled else "Lock when away off",
        )
        refresh = getattr(self.app, "action_refresh", None)
        if refresh is not None:
            self.app.call_from_thread(refresh)

    # ---- companion tools ------------------------------------------------

    @work(thread=True, exclusive=True, group="phone-tools-probe", exit_on_error=False)
    def probe_tools(self) -> None:
        """*Blocking* lookups and ``idevice_id -l`` stay off the UI thread."""
        snapshot = companion_tools.snapshot(self._tools_system)
        self.app.call_from_thread(self._tools_loaded, snapshot)

    def _tools_loaded(self, snapshot: companion_tools.Snapshot) -> None:
        self._tools = snapshot
        if self._needs_pairing:
            photos = snapshot.get(companion_tools.PHOTOS)
            self._needs_pairing = photos is not None and photos.enabled
        if self.is_attached:
            self.render_phone()

    def action_tool(self, action: str) -> None:
        if self._tool_busy:
            return
        if action == companion_tools.PAIR:
            usable = self._needs_pairing
        else:
            tool = self._tools.get(action) if self._tools is not None else None
            if tool is None and action == companion_tools.SEND and self._tools is not None:
                # The LocalSend plugin replaced the app (ReplacesTools).
                self.notify(companion_tools.REPLACED_SEND_HINT, markup=False)
                return
            usable = tool is not None and tool.enabled
            if tool is not None and not tool.enabled:
                self.notify(_plain(tool.subtitle), severity="warning", markup=False)
                return
        if not usable:
            return
        self._tool_busy = action
        self.render_phone()
        self._run_tool(action)

    @work(thread=True, group="phone-tool", exit_on_error=False)
    def _run_tool(self, action: str) -> None:
        result = companion_tools.perform(self._tools_system, action)
        self.app.call_from_thread(self._tool_done, action, result)

    def _tool_done(self, action: str, result: companion_tools.ActionResult) -> None:
        self._tool_busy = ""
        if result.needs_pairing:
            self._needs_pairing = True
        elif result.ok and action in (companion_tools.PHOTOS, companion_tools.PAIR):
            self._needs_pairing = False
        self.notify(
            _plain(result.message), severity="information" if result.ok else "warning",
            markup=False,
        )
        if self.is_attached:
            self.render_phone()
            self.probe_tools()

    @on(Button.Pressed, "#phone-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
