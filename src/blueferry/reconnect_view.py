"""Toolkit-neutral texts for the manual iPhone reconnect (all clients)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from blueferry.i18n import _

UNREACHABLE_TEXT = _("iPhone not reachable. Is Bluetooth switched on on the iPhone?")
STARTED_TEXT = _("Reconnecting to the iPhone…")
IN_PROGRESS_TEXT = _("A reconnect is already running.")
CONNECTED_TEXT = _("The iPhone is connected.")
BLUEZ_HUNG_TEXT = _(
    "The system Bluetooth service is not responding (stuck in the kernel). "
    "Restart the computer."
)
BLUEZ_UNRESPONSIVE_TEXT = _(
    "The system Bluetooth service is not responding. If this persists, restart the computer."
)
UNSUPPORTED_TEXT = _("The running BlueFerry service cannot reconnect on request.")


@dataclass(frozen=True, slots=True)
class ReconnectView:
    """Whether to offer "Reconnect", and the line shown next to it."""

    available: bool
    offered: bool
    hint: str


def _duration(seconds: int) -> str:
    if seconds < 60:
        return _("{seconds} s").format(seconds=seconds)
    return _("{minutes} min").format(minutes=(seconds + 59) // 60)


def reconnect_view(status: Mapping[str, Any] | None) -> ReconnectView:
    """Describe the Classic reconnect state from content-free status keys."""
    state = None if status is None else status.get("phone_reconnect_state")
    if not isinstance(state, str) or status is None or status.get("daemon") is False:
        return ReconnectView(False, False, UNSUPPORTED_TEXT)
    if status.get("bluez_unresponsive") is True:
        return ReconnectView(True, True, bluez_text(status))
    if state == "connected":
        return ReconnectView(True, False, CONNECTED_TEXT)
    if state == "connecting":
        return ReconnectView(True, False, STARTED_TEXT)
    raw_next = status.get("phone_reconnect_next_in_sec")
    next_in = raw_next if type(raw_next) is int and raw_next > 0 else 0
    if status.get("phone_reconnect_paused") is True:
        hint = _("Waiting for the iPhone; next automatic try in {time}.").format(
            time=_duration(next_in),
        )
    elif state == "unreachable" and next_in:
        hint = UNREACHABLE_TEXT + " " + _("Next automatic try in {time}.").format(
            time=_duration(next_in),
        )
    else:
        hint = _("Reconnecting automatically.")
    return ReconnectView(True, True, hint)


def bluez_text(status: Mapping[str, Any] | None) -> str:
    """Why nothing connects while bluetoothd does not answer."""
    reason = None if status is None else status.get("bluez_unresponsive_reason")
    return BLUEZ_HUNG_TEXT if reason == "kernel" else BLUEZ_UNRESPONSIVE_TEXT


def result_text(result: str) -> str:
    """User-facing text for a ReconnectPhone() reply."""
    return {
        "started": STARTED_TEXT,
        "in-progress": IN_PROGRESS_TEXT,
        "connected": CONNECTED_TEXT,
        "unreachable": UNREACHABLE_TEXT,
        "bluez-kernel": BLUEZ_HUNG_TEXT,
        "bluez-unresponsive": BLUEZ_UNRESPONSIVE_TEXT,
    }.get(result, UNREACHABLE_TEXT)
