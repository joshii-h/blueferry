"""blueferry-tray: pure presenter logic plus an offscreen smoke test."""
from __future__ import annotations

import os
import subprocess  # nosec B404 - runs the smoke script in a fresh process
import sys
import textwrap
from pathlib import Path

import pytest

from blueferry.models import BackendStatus, Thread
from blueferry.qt import tray_presenter as presenter
from blueferry.tether_status import TetherStatus

ROOT = Path(__file__).resolve().parents[1]


def _thread(*read_flags: bool, outgoing: bool = False) -> Thread:
    return Thread.from_dict({
        "key": "t",
        "messages": [
            {"handle": str(index), "body": "", "read": read, "outgoing": outgoing}
            for index, read in enumerate(read_flags)
        ],
    })


def test_unread_total_counts_incoming_unread_messages_only() -> None:
    threads = [_thread(False, True, False), _thread(False, outgoing=True), _thread(True)]
    assert presenter.unread_total(threads) == 2
    assert presenter.unread_total([]) == 0


@pytest.mark.parametrize(("count", "text"), [(0, ""), (-1, ""), (1, "1"), (99, "99"), (100, "99+")])
def test_badge_text(count: int, text: str) -> None:
    assert presenter.badge_text(count) == text


def test_tooltip_lists_connection_battery_signal_network_and_unread() -> None:
    assert presenter.tooltip(None, 0) == "BlueFerry service is not running"
    assert presenter.tooltip(None, 0, "needs update") == "needs update"
    status = BackendStatus.from_dict({
        "map": True, "phone_battery_percent": 87, "phone_battery_source": "ble",
        "phone_signal_strength": 60, "phone_network_name": "Sunrise",
        "phone_network_status": "registered",
    })
    assert presenter.tooltip(status, 3).splitlines() == [
        "iPhone connected", "Battery: 87 %", "Signal: 60 %", "Network: Sunrise",
        "3 unread messages",
    ]
    offline = BackendStatus.from_dict({"phone_battery_level": 40})
    assert presenter.tooltip(offline, 1).splitlines() == [
        "iPhone offline", "Battery: about 40 %", "1 unread message",
    ]
    connecting = BackendStatus.from_dict({"initializing": True})
    assert presenter.tooltip(connecting, 0) == "Connecting…"


def test_audio_toggle_follows_the_confirmed_route() -> None:
    assert not presenter.audio_toggle(None).visible
    assert not presenter.audio_toggle({}).visible  # backend without the switch
    on_pc = presenter.audio_toggle({"phone_audio_route": "pc"})
    assert (on_pc.visible, on_pc.enabled, on_pc.checked) == (True, True, True)
    on_phone = presenter.audio_toggle({"phone_audio_route": "phone"})
    assert (on_phone.enabled, on_phone.checked) == (True, False)
    pending = presenter.audio_toggle({"phone_audio_route": "phone", "phone_audio_pending": "pc"})
    assert pending.enabled is False
    unavailable = presenter.audio_toggle({
        "phone_audio_route": "unavailable", "phone_audio_reason": "phone_disconnected",
    })
    assert (unavailable.visible, unavailable.enabled) == (True, False)
    assert presenter.audio_route_for(True) == "pc"
    assert presenter.audio_route_for(False) == "phone"


def test_hotspot_toggle() -> None:
    assert not presenter.hotspot_toggle(None).visible
    off = presenter.hotspot_toggle(TetherStatus())
    assert (off.visible, off.enabled, off.checked) == (True, True, False)
    connected = presenter.hotspot_toggle(TetherStatus(state="connected"))
    assert (connected.enabled, connected.checked) == (True, True)
    connecting = presenter.hotspot_toggle(TetherStatus(state="connecting"))
    assert (connecting.enabled, connecting.checked) == (False, True)


_SMOKE = textwrap.dedent("""
    import json, sys
    from PySide6.QtDBus import QDBusConnection
    from PySide6.QtWidgets import QApplication
    from blueferry.protocol import MESSAGES_API_VERSION
    from blueferry.qt.tray import TrayController, badged_icon, phone_icon

    app = QApplication([])
    address = sys.argv[1]
    bus = (QDBusConnection.connectToBus(address, "tray-smoke") if address
           else QDBusConnection("not-connected"))
    launches = []
    tray = TrayController(bus, launch=lambda: launches.append(1) or True)
    tray.start()
    app.processEvents()
    # No daemon on the bus: offline, and nothing was activated.
    assert tray.status is None, tray.status
    assert tray.tray.toolTip() == "BlueFerry service is not running", tray.tray.toolTip()
    assert not tray.audio_action.isVisible() and not tray.hotspot_action.isVisible()

    tray.apply_status(json.dumps({
        "api_version": MESSAGES_API_VERSION, "map": True,
        "phone_battery_percent": 87, "phone_battery_source": "ble",
        "phone_audio_route": "pc",
    }))
    tray.apply_threads(json.dumps([{"key": "a", "messages": [
        {"handle": "1", "body": "", "read": False},
        {"handle": "2", "body": "", "read": False}]}]))
    tray.apply_tether(json.dumps({"state": "connected"}))
    lines = tray.tray.toolTip().splitlines()
    assert lines == ["iPhone connected", "Battery: 87 %", "2 unread messages"], lines
    assert tray.audio_action.isChecked() and tray.audio_action.isEnabled()
    assert tray.hotspot_action.isChecked() and tray.hotspot_action.isVisible()
    texts = [action.text() for action in tray.menu.actions() if not action.isSeparator()]
    assert texts[0] == "Open BlueFerry" and texts[-1] == "Quit", texts

    tray.apply_status(json.dumps({"api_version": 0}))
    assert "needs an update" in tray.tray.toolTip()

    tray.open_app()  # no Qt client registered: start one
    assert launches == [1]

    badge = badged_icon(phone_icon(), "3").pixmap(64, 64).toImage()
    red = badge.pixelColor(56, 6)
    assert red.red() > 180 and red.green() < 120, red.name()
    assert badged_icon(phone_icon(), "").pixmap(64, 64).toImage().pixelColor(63, 0).alpha() == 0
    tray.close()
    print("SMOKE-OK")
""")


@pytest.mark.parametrize("use_private_bus", [False, True])
def test_tray_smoke_offscreen(use_private_bus: bool) -> None:
    address = os.environ.get("BLUEFERRY_TEST_DBUS_ADDRESS", "") if use_private_bus else ""
    if use_private_bus and not address:
        pytest.skip("needs the private dbus-run-session test bus")
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT / "src"),
        "LANGUAGE": "C",
    }
    if not use_private_bus:
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/blueferry-tests-no-live-bus"
    result = subprocess.run(  # nosec B603 - fixed interpreter and script
        [sys.executable, "-c", _SMOKE, address],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert "SMOKE-OK" in result.stdout, result.stdout + result.stderr
