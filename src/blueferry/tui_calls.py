"""Optional phone-call panel for the Textual client (Calls1).

The panel is a modal screen opened with ``c``. It polls ListCalls while it is
open and runs every blocking backend call in a worker thread, so the terminal
never waits on oFono or the phone.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Protocol

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from blueferry import phone_overview
from blueferry.client import BackendError
from blueferry.models import CALLS_STATE_TEXT, CallInfo, CallsSnapshot
from blueferry.text_safety import terminal_text

_POLL_SECONDS = 2.0
_MAX_NUMBER = 96
_RINGING = frozenset({"incoming", "waiting"})



class CallsClient(Protocol):
    def calls(self) -> CallsSnapshot: ...

    def dial(self, number: str) -> str: ...

    def answer_call(self, call_id: str) -> None: ...

    def hangup_call(self, call_id: str) -> None: ...

    def swap_calls(self) -> None: ...


def call_line(call: CallInfo) -> str:
    peer = terminal_text(call.display_peer).replace("\n", " ")
    number = terminal_text(call.number)
    detail = f"  {number}" if number and number != peer else ""
    return f"{call.state.upper():<9} {peer}{detail}"


def describe(snapshot: CallsSnapshot) -> tuple[str, str]:
    """Return the panel's state line and call list text."""
    state = CALLS_STATE_TEXT.get(snapshot.state, snapshot.state)
    if not snapshot.calls:
        return state, "No active calls"
    return state, "\n".join(call_line(call) for call in snapshot.calls)


def ringing_call(snapshot: CallsSnapshot) -> str | None:
    ringing = [call.call_id for call in snapshot.calls if call.state in _RINGING]
    return ringing[0] if len(ringing) == 1 else None


_HANGUP_PRIORITY = (
    ("active",), ("held",), ("incoming", "waiting"), ("dialing", "alerting"),
)


def hangup_target(snapshot: CallsSnapshot) -> CallInfo | None:
    """Pick the one call the hang-up button ends: active, then held.

    A ringing call is declined only when nothing is connected, and the panel
    never falls back to "hang up all".
    """
    for states in _HANGUP_PRIORITY:
        for call in snapshot.calls:
            if call.state in states:
                return call
    return None


def recent_options(rows: list[dict]) -> list[Option]:
    """Recent calls grouped by day: a disabled header per day, then rows."""
    options: list[Option] = []
    day = None
    for index, row in enumerate(rows):
        if row["day"] != day:
            day = row["day"]
            options.append(Option(Text(terminal_text(day), style="bold #7dd3fc"),
                                  id=f"day:{index}", disabled=True))
        count = f" ({row['count']})" if row["count"] > 1 else ""
        direction = {"missed": "Missed", "outgoing": "Outgoing"}.get(row["direction"], "Incoming")
        line = Text(f"  {row['clock']:>8}  {direction:<9} ", style="dim")
        line.append(terminal_text(row["caller"]).replace("\n", " ") + count,
                    style="bold #fda4af" if row["missed"] else "")
        options.append(Option(line, id=f"call:{index}"))
    return options


class CallsScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("b", "call_back", "Call back", show=False),
        Binding("h", "hold", "Hold/resume", show=False),
    ]

    def __init__(self, client: CallsClient, *, poll_seconds: float = _POLL_SECONDS) -> None:
        super().__init__()
        self._client = client
        self._poll_seconds = poll_seconds
        self.snapshot = CallsSnapshot(state="unknown")
        self._recent: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="calls-dialog", classes="dialog"):
            yield Static("Phone calls (experimental)", classes="dialog-title")
            yield Static("Loading…", id="calls-state")
            yield Static("", id="calls-list", classes="dialog-copy")
            yield Static("Number", classes="field-label")
            yield Input(placeholder="+1 555 123 4567", id="calls-number", max_length=_MAX_NUMBER)
            yield Static("Recent calls (Enter or b calls back · h hold/resume)",
                         classes="field-label")
            yield OptionList(id="calls-recent")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="calls-close")
                yield Button("Hang up", variant="error", id="calls-hangup")
                yield Button("Answer", variant="success", id="calls-answer")
                yield Button("Dial", variant="primary", id="calls-dial")

    def on_mount(self) -> None:
        self.query_one("#calls-number", Input).focus()
        self.refresh_calls()
        self.load_recent()
        self.set_interval(self._poll_seconds, self.refresh_calls)

    @work(thread=True, exclusive=True, group="calls-refresh", exit_on_error=False)
    def refresh_calls(self) -> None:
        try:
            snapshot = self._client.calls()
        except BackendError as error:
            self.app.call_from_thread(self._show_error, str(error))
            return
        self.app.call_from_thread(self._show, snapshot)

    @work(thread=True, exclusive=True, group="calls-recent", exit_on_error=False)
    def load_recent(self) -> None:
        loader = getattr(self._client, "call_history", None)
        try:
            entries = loader(200) if loader is not None else []
        except BackendError:
            entries = []
        self.app.call_from_thread(self._show_recent, phone_overview.call_groups(entries))

    def _show_recent(self, rows: list[dict]) -> None:
        self._recent = rows
        recent = self.query_one("#calls-recent", OptionList)
        recent.clear_options()
        if rows:
            recent.add_options(recent_options(rows))
        else:
            recent.add_option(Option("No recent calls (or recent calls are off)", disabled=True))

    def _highlighted_address(self) -> str:
        recent = self.query_one("#calls-recent", OptionList)
        if recent.highlighted is None:
            return ""
        option_id = recent.get_option_at_index(recent.highlighted).id or ""
        if not option_id.startswith("call:"):
            return ""
        return str(self._recent[int(option_id[5:])]["address"])

    @on(OptionList.OptionSelected, "#calls-recent")
    def action_call_back(self) -> None:
        address = self._highlighted_address()
        if not address:
            return
        if not self.snapshot.available:
            self.notify("Calls are not ready yet.", severity="warning")
            return
        self.query_one("#calls-number", Input).value = address
        self._run(lambda: self._client.dial(address), "Calling back…")

    def action_hold(self) -> None:
        if not any(call.state in ("active", "held") for call in self.snapshot.calls):
            return
        self._run(self._client.swap_calls, "Switched hold")

    def _show(self, snapshot: CallsSnapshot) -> None:
        self.snapshot = snapshot
        state, calls = describe(snapshot)
        self.query_one("#calls-state", Static).update(Text(state))
        self.query_one("#calls-list", Static).update(Text(calls))
        ready = snapshot.available
        self.query_one("#calls-dial", Button).disabled = not ready
        self.query_one("#calls-answer", Button).disabled = ringing_call(snapshot) is None
        target = hangup_target(snapshot)
        hangup = self.query_one("#calls-hangup", Button)
        hangup.disabled = target is None
        hangup.label = "Decline" if target is not None and target.ringing else "Hang up"

    def _show_error(self, message: str) -> None:
        self.query_one("#calls-state", Static).update(Text(terminal_text(message)))
        for selector in ("#calls-dial", "#calls-answer", "#calls-hangup"):
            self.query_one(selector, Button).disabled = True

    @work(thread=True, group="calls-action", exit_on_error=False)
    def _run(self, action: Callable[[], object], done: str) -> None:
        try:
            action()
        except BackendError as error:
            message = terminal_text(str(error))
            self.app.call_from_thread(self.notify, message, severity="error")
        else:
            self.app.call_from_thread(self.notify, done)
        self.app.call_from_thread(self.refresh_calls)

    @on(Button.Pressed, "#calls-dial")
    @on(Input.Submitted, "#calls-number")
    def dial(self) -> None:
        number = self.query_one("#calls-number", Input).value.strip()
        if not number:
            self.notify("Enter a number to dial", severity="warning")
            return
        self._run(lambda: self._client.dial(number), "Dialing…")

    @on(Button.Pressed, "#calls-answer")
    def answer(self) -> None:
        call_id = ringing_call(self.snapshot)
        if call_id is not None:
            self._run(lambda: self._client.answer_call(call_id), "Answered")

    @on(Button.Pressed, "#calls-hangup")
    def hangup(self) -> None:
        target = hangup_target(self.snapshot)
        if target is None:
            return
        call_id = target.call_id
        self._run(lambda: self._client.hangup_call(call_id), "Hung up")

    @on(Button.Pressed, "#calls-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
