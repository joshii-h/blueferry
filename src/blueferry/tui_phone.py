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
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from blueferry import companion_tools
from blueferry import phone_overview as overview
from blueferry.client import BackendError
from blueferry.models import BackendStatus, CallHistoryEntry, phone_status_fields
from blueferry.tether_status import TetherStatus
from blueferry.text_safety import terminal_text

_MAX_ROWS = 50
# Keys of the companion tools in the overview screen, by action.
TOOL_KEYS = {"mirror": "m", "send": "f", "photos": "i", "eject": "e", "pair": "y"}


class PhoneClient(Protocol):
    def call_history(self, limit: int = 200) -> list[CallHistoryEntry]: ...

    def notifications(self, limit: int = 50) -> dict: ...

    def set_proximity_lock(self, enabled: bool, grace_seconds: int) -> dict: ...


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
    ]
    text = Text()
    for key, label, on_, usable, hint in lines:
        state = "ON " if on_ else "OFF"
        text.append(f"[{key}] {state}  {label}\n", style="" if usable else "dim")
        text.append(f"      {_plain(hint)}\n", style="dim")
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
    rows = overview.call_rows(entries[:_MAX_ROWS])
    if not rows:
        return Text("No recent calls", style="dim")
    text = Text()
    for row in rows:
        text.append(
            f"{row['direction']:<9} {_plain(row['caller'])}  {_plain(row['time'])}\n",
            style="bold #fda4af" if row["missed"] else "",
        )
    text.append("Press c to dial.", style="dim")
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
        return Text("No notifications yet", style="dim")
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
    ) -> None:
        super().__init__()
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
            yield Static("iPhone", classes="dialog-title")
            with VerticalScroll(id="phone-body"):
                yield Static("Switches", classes="field-label")
                yield Static("", id="phone-switches", classes="dialog-copy")
                yield Static("Tools", classes="field-label")
                yield Static("", id="phone-tools", classes="dialog-copy")
                yield Static("Recent calls", classes="field-label")
                yield Static("", id="phone-calls", classes="dialog-copy")
                yield Static("Notifications", classes="field-label")
                yield Static("", id="phone-notifications", classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="phone-close")

    def on_mount(self) -> None:
        self.render_phone()
        self.reload()
        self.probe_tools()

    def render_phone(self) -> None:
        status = self._status().to_dict()
        self.query_one("#phone-switches", Static).update(switches_text(status, self._tether()))
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
