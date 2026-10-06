"""Pure presentation helpers for the Qt phone overview (card and tabs).

Kept free of Qt so the wording and the opt-in hints are unit-testable. Every
string returned here is plain text; QML renders it with Text.PlainText.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from blueferry.i18n import _
from blueferry.time_display import format_message_timestamp

LOCAL_ENV = "~/.config/blueferry/local.env"


def _opt_in(variable: str) -> str:
    return _("Set {variable}=true in {path} and restart the backend.").format(
        variable=variable, path=LOCAL_ENV,
    )


def phone_audio(status: Mapping[str, Any]) -> dict[str, object]:
    """The card's PC/iPhone sound switch, derived from GetStatus."""
    route = status.get("phone_audio_route")
    if route is None:
        return {
            "supported": False, "available": False, "onPc": False,
            "pending": False,
            "hint": _("Update the BlueFerry backend to switch the iPhone's sound here."),
        }
    pending = status.get("phone_audio_pending") in ("pc", "phone")
    if route == "pc":
        hint = _("iPhone sound plays on this computer.")
    elif route == "phone":
        hint = _("iPhone sound plays on the iPhone.")
    else:
        reason = status.get("phone_audio_reason")
        if reason == "keep_phone_audio_on_phone":
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
    if pending:
        hint = _("Switching the iPhone's sound…")
    return {
        "supported": True,
        "available": route in ("pc", "phone"),
        "onPc": route == "pc",
        "pending": pending,
        "hint": hint,
    }


def _phone_status_wait(calls_state: object) -> str:
    """Why battery and signal are missing although calls are enabled."""
    if calls_state in ("searching", "connecting"):
        return _("Calls and battery: connecting to the iPhone…")
    if calls_state == "unavailable":
        return _("Calls and battery unavailable: oFono is not running or denies access.")
    return _("Waiting for battery and signal from the iPhone.")


def feature_hints(status: Mapping[str, Any]) -> dict[str, str]:
    """Why an opt-in feature is greyed out, keyed by feature; empty when usable."""
    hints: dict[str, str] = {}
    calls = status.get("calls_enabled") is True
    if not status.get("media_control_enabled"):
        hints["media"] = _opt_in("BLUEFERRY_MEDIA_CONTROL_ENABLED")
    if not calls:
        hints["phoneStatus"] = _("Battery and signal need phone calls. ") + _opt_in(
            "BLUEFERRY_CALLS_ENABLED"
        )
        hints["calls"] = _opt_in("BLUEFERRY_CALLS_ENABLED")
    elif not isinstance(status.get("phone_battery_level"), int | float) and not isinstance(
        status.get("phone_signal_strength"), int | float
    ):
        hints["phoneStatus"] = _phone_status_wait(status.get("calls_state"))
    if status.get("call_history_enabled") is not True:
        hints["callHistory"] = _opt_in("BLUEFERRY_CALL_HISTORY_ENABLED")
    if status.get("notification_history_enabled") is not True:
        hints["notifications"] = _opt_in("BLUEFERRY_NOTIFICATION_HISTORY") + " " + _(
            "Other apps' notifications also need the All iPhone Notifications policy."
        )
    if "proximity_lock" not in status:
        hints["proximity"] = _(
            "Update the BlueFerry backend to lock the desktop when the iPhone goes away."
        )
    return hints


def notification_rows(records: object) -> list[dict[str, str]]:
    """Normalize ListNotifications records for the Notifications tab."""
    rows: list[dict[str, str]] = []
    if not isinstance(records, list):
        return rows
    for record in records:
        if not isinstance(record, Mapping):
            continue
        app = str(record.get("app_name") or record.get("app_id") or _("iPhone app"))
        rows.append({
            "app": app,
            "time": format_message_timestamp(str(record.get("time") or "")),
            "title": str(record.get("title") or ""),
            "subtitle": str(record.get("subtitle") or ""),
            "body": str(record.get("body") or ""),
        })
    return rows
