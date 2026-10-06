"""Toolkit-free phone overview rules shared by the TUI, GTK and Quickshell."""
from __future__ import annotations

from blueferry import phone_overview as overview
from blueferry.models import BackendStatus, CallHistoryEntry, phone_status_fields
from blueferry.tether_status import TetherStatus


def test_exact_battery_percent_wins_over_the_hfp_level_when_present() -> None:
    status = BackendStatus.from_dict({"phone_battery_level": 60, "phone_battery_percent": 57})
    assert phone_status_fields(status, include_network=False) == [("Battery", "57 %")]
    invalid = BackendStatus.from_dict({"phone_battery_level": 60, "phone_battery_percent": True})
    assert phone_status_fields(invalid, include_network=False) == [("Battery", "about 60 %")]


def test_phone_status_hint_explains_the_opt_in_and_the_wait() -> None:
    assert "BLUEFERRY_CALLS_ENABLED=true" in overview.phone_status_hint({})
    assert "oFono" in overview.phone_status_hint(
        {"calls_enabled": True, "calls_state": "unavailable"}
    )
    assert overview.phone_status_hint({"calls_enabled": True, "phone_battery_percent": 5}) == ""
    assert overview.phone_status_hint({"calls_enabled": True, "phone_signal_strength": 0}) == ""


def test_now_playing_disabled_unavailable_and_playing() -> None:
    disabled = overview.now_playing({"enabled": False})
    assert not disabled["available"]
    assert "BLUEFERRY_MEDIA_CONTROL_ENABLED=true" in disabled["hint"]
    waiting = overview.now_playing(
        {"enabled": True, "available": False, "detail": "waiting-for-iphone"}
    )
    assert waiting["enabled"] and "Waiting" in waiting["hint"]

    playing = overview.now_playing({
        "enabled": True, "available": True,
        "player": {"state": "playing"},
        "track": {"title": "<b>Song</b>", "artist": "[red]Band"},
        "supported_commands": ["pause", "next", "volume-up"],
    })
    assert playing["playing"]
    assert playing["title"] == "<b>Song</b> — [red]Band"  # verbatim; clients escape
    assert playing["commands"] == {"toggle", "next"}

    idle = overview.now_playing({
        "enabled": True, "available": True, "player": {}, "supported_commands": "x",
    })
    assert idle["title"] == "Nothing is playing on the iPhone."
    assert idle["commands"] == frozenset()


def test_phone_audio_route_states() -> None:
    assert not overview.phone_audio({})["supported"]
    on_pc = overview.phone_audio({"phone_audio_route": "pc"})
    assert on_pc["available"] and on_pc["on_pc"]
    pending = overview.phone_audio({"phone_audio_route": "phone", "phone_audio_pending": "pc"})
    assert pending["pending"] and not pending["available"]
    kept = overview.phone_audio(
        {"phone_audio_route": "", "phone_audio_reason": "keep_phone_audio_on_phone"}
    )
    assert not kept["available"]
    assert "BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false" in kept["hint"]


def test_tether_and_proximity_switches() -> None:
    assert not overview.tether(None)["available"]
    connected = overview.tether(TetherStatus(state="connected"))
    assert connected["available"] and connected["active"]
    assert not overview.tether(TetherStatus(state="connecting"))["available"]

    assert not overview.proximity({})["available"]
    enabled = overview.proximity({
        "proximity_lock": "present", "proximity_lock_enabled": True,
        "proximity_lock_grace_sec": 30,
    })
    assert enabled["enabled"] and enabled["grace"] == 30 and "30 s" in enabled["hint"]
    off = overview.proximity({"proximity_lock": "off", "proximity_lock_grace_sec": True})
    assert not off["enabled"] and off["grace"] == 0


def test_history_hints_and_le_bond() -> None:
    assert "BLUEFERRY_CALL_HISTORY_ENABLED" in overview.call_history_hint({})
    assert overview.call_history_hint({"call_history_enabled": True}) == ""
    assert "BLUEFERRY_NOTIFICATION_HISTORY" in overview.notifications_hint({})
    assert overview.notifications_hint({"notification_history_enabled": True}) == ""
    assert overview.le_bond_hint({}) == ""
    assert "pairing looks outdated" in overview.le_bond_hint({"le_bond_suspect": True})


def test_call_and_notification_rows_keep_remote_text_verbatim() -> None:
    entry = CallHistoryEntry.from_dict({
        "direction": "missed", "timestamp": "2026-08-10T10:00:00+00:00",
        "address": "+41790000000", "name": "<i>Ann</i>",
    })
    assert entry is not None
    (row,) = overview.call_rows([entry])
    assert row["caller"] == "<i>Ann</i>" and row["missed"] and row["direction"] == "Missed"

    rows = overview.notification_rows([
        {"app_name": "Mail", "title": "[b]Hi", "body": "", "subtitle": "sub",
         "time": "2026-08-10T10:00:00+00:00"},
        "garbage",
        {"app_id": "com.example"},
    ])
    assert [r["app"] for r in rows] == ["Mail", "com.example"]
    assert rows[0]["title"] == "[b]Hi" and rows[0]["body"] == "sub"
    assert overview.notification_rows(None) == []
