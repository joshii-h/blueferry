"""GTK iPhone overview: presentation rules and off-thread client calls."""
from __future__ import annotations

import threading

from blueferry.client import TetherUnsupportedError
from blueferry.tether_status import TetherStatus
from blueferry.ui import client as client_module
from blueferry.ui.phone_presenter import media, phone_rows, switches
from tests.test_ui_client import _Bus, _UnconfiguredSetup


def test_phone_rows_fall_back_to_the_opt_in_hint() -> None:
    assert phone_rows({
        "phone_battery_percent": 42, "phone_battery_source": "ble", "phone_signal_strength": 60,
    }) == [
        ("Battery", "42 %"), ("Signal", "60 %"),
    ]
    ((label, hint),) = phone_rows({})
    assert label == "Battery and Signal" and "BLUEFERRY_CALLS_ENABLED" in hint


def test_switches_are_insensitive_with_hints_when_unavailable() -> None:
    off = switches({}, None)
    assert not any(state.sensitive for state in off.values())
    assert all(state.subtitle for state in off.values())

    on = switches(
        {"phone_audio_route": "pc", "proximity_lock": "present",
         "proximity_lock_enabled": True, "proximity_lock_grace_sec": 10},
        TetherStatus(state="connected"),
    )
    assert all(state.active and state.sensitive for state in on.values())


def test_media_state_keeps_remote_title_verbatim_and_maps_buttons() -> None:
    disabled = media({"enabled": False})
    assert not any(disabled.buttons.values())
    assert "BLUEFERRY_MEDIA_CONTROL_ENABLED" in disabled.subtitle

    state = media({
        "enabled": True, "available": True, "player": {"state": "paused"},
        "track": {"title": "<b>A & B</b>"}, "supported_commands": ["play", "next"],
    })
    assert state.title == "<b>A & B</b>"
    assert state.subtitle == "Paused" and not state.playing
    assert dict(state.buttons) == {"previous": False, "toggle": True, "next": True}


def test_phone_calls_run_off_the_ui_thread_and_tolerate_old_backends(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "get_session_bus", _Bus)
    monkeypatch.setattr(client_module, "SetupClient", _UnconfiguredSetup)
    monkeypatch.setattr(
        client_module.DaemonClient, "ensure_backend_current_async", lambda self: None,
    )
    monkeypatch.setattr(
        client_module.GLib, "idle_add", lambda callback, *args: callback(*args) or 1,
    )
    main = threading.get_ident()
    seen: list[tuple] = []

    class Backend:
        def tether_state(self):
            raise TetherUnsupportedError("old backend")

        def set_proximity_lock(self, enabled, grace):
            assert threading.get_ident() != main
            seen.append(("lock", enabled, grace))
            return {}

    client = client_module.DaemonClient()
    monkeypatch.setattr(client, "_call_backend", lambda operation: operation(Backend()))
    done = threading.Event()
    results: list[object] = []
    try:
        client.tether_state_async(lambda value: (results.append(value), done.set()))
        assert done.wait(3)
        done.clear()
        client.set_proximity_lock_async(True, 30, lambda _v: done.set(), lambda _m: done.set())
        assert done.wait(3)
    finally:
        client.stop()
    assert results == [None]
    assert seen == [("lock", True, 30)]
