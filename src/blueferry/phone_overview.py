"""Toolkit-free phone overview rules shared by the TUI, GTK and Quickshell.

The Qt client keeps its own copy in ``blueferry.qt.phone_link`` because it
ships as a separate package. Every string returned here is plain text taken
verbatim from the iPhone or the backend; each client escapes it for its own
renderer (Rich ``Text``, GTK labels without markup, QML ``Text.PlainText``).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from blueferry.i18n import _
from blueferry.models import CallHistoryEntry
from blueferry.tether_status import TetherStatus
from blueferry.time_display import format_message_timestamp

LOCAL_ENV = "~/.config/blueferry/local.env"
MEDIA_BUTTONS = ("previous", "toggle", "next")



def _media_detail(detail: str) -> str:
    if detail == "requires-notification-access-mode":
        return _(
            "iPhone media control needs the Bluetooth LE link, which the "
            "compatibility pairing mode does not use."
        )
    if detail == "waiting-for-iphone":
        return _("Waiting for the iPhone's media service on the Bluetooth LE link.")
    return _("iPhone media control is not available.")


def opt_in(variable: str) -> str:
    return _("Set {variable}=true in {path} and restart the backend.").format(
        variable=variable, path=LOCAL_ENV,
    )


def _section(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def now_playing(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Title line, play state and usable buttons for one GetNowPlaying snapshot."""
    snapshot = snapshot or {}
    if not snapshot.get("enabled"):
        return {
            "enabled": False, "available": False, "playing": False, "title": "",
            "commands": frozenset(), "hint": opt_in("BLUEFERRY_MEDIA_CONTROL_ENABLED"),
        }
    if not snapshot.get("available"):
        detail = str(snapshot.get("detail") or "")
        return {
            "enabled": True, "available": False, "playing": False, "title": "",
            "commands": frozenset(),
            "hint": _media_detail(detail),
        }
    player, track = _section(snapshot, "player"), _section(snapshot, "track")
    title = str(track.get("title") or "")
    artist = str(track.get("artist") or "")
    playing = player.get("state") == "playing"
    raw = snapshot.get("supported_commands")
    supported = {str(item) for item in raw} if isinstance(raw, list) else set()
    commands: set[str] = {name for name in ("previous", "next") if name in supported}
    if supported & {"toggle", "pause" if playing else "play"}:
        commands.add("toggle")
    if title:
        line = _("{title} — {artist}").format(title=title, artist=artist) if artist else title
    else:
        line = _("Nothing is playing on the iPhone.")
    return {
        "enabled": True, "available": True, "playing": playing, "title": line,
        "commands": frozenset(commands), "hint": "",
    }


def phone_audio(status: Mapping[str, Any]) -> dict[str, Any]:
    """The PC/iPhone sound switch, derived from GetStatus."""
    route = status.get("phone_audio_route")
    if route is None:
        return {
            "supported": False, "available": False, "on_pc": False, "pending": False,
            "hint": _("Update the BlueFerry backend to switch the iPhone's sound here."),
        }
    pending = status.get("phone_audio_pending") in ("pc", "phone")
    reason = status.get("phone_audio_reason")
    if pending:
        hint = _("Switching the iPhone's sound…")
    elif route == "pc":
        hint = _("iPhone sound plays on this computer.")
    elif route == "phone":
        hint = _("iPhone sound plays on the iPhone.")
    elif reason == "keep_phone_audio_on_phone":
        hint = _(
            "Set BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false in {path} and "
            "restart the backend to allow iPhone sound here."
        ).format(path=LOCAL_ENV)
    elif reason == "phone_disconnected":
        hint = _("Available while the iPhone is connected.")
    elif reason == "no_a2dp_source":
        hint = _("The iPhone does not offer Bluetooth audio to this computer.")
    else:
        hint = _("Checking the iPhone's audio connection…")
    return {
        "supported": True, "available": route in ("pc", "phone") and not pending,
        "on_pc": route == "pc", "pending": pending, "hint": hint,
    }


def tether(value: TetherStatus | None) -> dict[str, Any]:
    """The hotspot switch; ``value`` is None when the backend has no Tether1."""
    if value is None:
        return {
            "available": False, "active": False,
            "hint": _("Update the BlueFerry backend to use the iPhone's Personal Hotspot."),
        }
    return {"available": value.settled, "active": value.active, "hint": value.summary()}


def proximity(status: Mapping[str, Any]) -> dict[str, Any]:
    """The lock-when-away switch from the Presence1 keys in GetStatus."""
    if "proximity_lock" not in status:
        return {
            "available": False, "enabled": False, "grace": 0,
            "hint": _(
                "Update the BlueFerry backend to lock the desktop when the iPhone goes away."
            ),
        }
    grace = status.get("proximity_lock_grace_sec")
    grace = grace if isinstance(grace, int) and not isinstance(grace, bool) else 0
    enabled = status.get("proximity_lock_enabled") is True
    hint = (
        _("Locks the desktop {seconds} s after the iPhone goes away.").format(seconds=grace)
        if enabled else _("Off. The desktop stays unlocked when the iPhone goes away.")
    )
    return {"available": True, "enabled": enabled, "grace": grace, "hint": hint}


def phone_status_hint(status: Mapping[str, Any]) -> str:
    """Why battery and signal are missing; empty when at least one is known."""
    if status.get("calls_enabled") is not True:
        return _("Battery and signal need phone calls. ") + opt_in("BLUEFERRY_CALLS_ENABLED")
    if any(
        isinstance(status.get(key), int) and not isinstance(status.get(key), bool)
        for key in ("phone_battery_percent", "phone_battery_level", "phone_signal_strength")
    ):
        return ""
    calls_state = status.get("calls_state")
    if calls_state in ("searching", "connecting"):
        return _("Calls and battery: connecting to the iPhone…")
    if calls_state == "unavailable":
        return _("Calls and battery unavailable: oFono is not running or denies access.")
    return _("Waiting for battery and signal from the iPhone.")


def le_bond_hint(status: Mapping[str, Any]) -> str:
    if status.get("le_bond_suspect") is not True:
        return ""
    return _(
        "The iPhone's Bluetooth pairing looks outdated, so notifications can't "
        "connect. Forget this computer on the iPhone, remove the iPhone here, "
        "and pair again. Details: blueferry doctor"
    )


def call_history_hint(status: Mapping[str, Any]) -> str:
    if status.get("call_history_enabled") is True:
        return ""
    return opt_in("BLUEFERRY_CALL_HISTORY_ENABLED")


def notifications_hint(status: Mapping[str, Any]) -> str:
    if status.get("notification_history_enabled") is True:
        return ""
    return opt_in("BLUEFERRY_NOTIFICATION_HISTORY") + " " + _(
        "Other apps' notifications also need the All iPhone Notifications policy."
    )


def call_rows(entries: Iterable[CallHistoryEntry]) -> list[dict[str, Any]]:
    labels = {"missed": _("Missed"), "incoming": _("Incoming"), "outgoing": _("Outgoing")}
    return [
        {
            "caller": entry.display_caller,
            "number": entry.address,
            "direction": labels.get(entry.direction, entry.direction),
            "missed": entry.missed,
            "time": entry.display_time,
        }
        for entry in entries
    ]


def notification_rows(records: object) -> list[dict[str, str]]:
    """Normalize ListNotifications records (the ``notifications`` list)."""
    rows: list[dict[str, str]] = []
    if not isinstance(records, list):
        return rows
    for record in records:
        if not isinstance(record, Mapping):
            continue
        rows.append({
            "app": str(record.get("app_name") or record.get("app_id") or _("iPhone app")),
            "time": format_message_timestamp(str(record.get("time") or "")),
            "title": str(record.get("title") or ""),
            "body": str(record.get("body") or record.get("subtitle") or ""),
        })
    return rows
