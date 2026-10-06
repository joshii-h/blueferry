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
    tray.tray.setVisible(True)  # so hiding must come from the bus reply
    tray.start()
    import time
    for _ in range(100):  # the ListActivatableNames reply is asynchronous
        app.processEvents()
        time.sleep(0.01)
    # Private test bus: BlueFerry is not activatable, so the icon hides.
    # No bus at all: unknown, so it stays.
    assert tray.tray.isVisible() is (not address), tray.tray.isVisible()
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
    # Companion tools are only probed when the menu opens; nothing ran yet.
    assert set(tray.tool_actions) == {"mirror", "send", "photos", "eject", "pair"}
    assert not tray.tool_actions["pair"].isVisible()
    assert tray.companion.state()["probed"] is False

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


def test_tool_entries_grey_out_missing_tools_and_show_eject_only_when_mounted() -> None:
    from blueferry.companion_tools import PhotoProbe, photos_states
    from blueferry.companion_tools import ToolState as Tool

    photos, eject = photos_states(PhotoProbe(installed=True, device=True, mounted=True))
    tools = [
        Tool("mirror", True, True, "Mirror iPhone screen", ""),
        Tool("send", False, False, "Send a file (LocalSend)", ""),
        photos, eject,
    ]
    entries = {entry.key: entry for entry in presenter.tool_entries(tools)}
    assert entries["mirror"].enabled and entries["mirror"].visible
    assert entries["send"].visible and not entries["send"].enabled
    assert entries["send"].text == "Send a file (LocalSend) (not installed)"
    assert entries["eject"].visible and entries["eject"].enabled
    assert not entries["pair"].visible

    busy = {entry.key: entry for entry in presenter.tool_entries(
        tools, busy="photos", needs_pairing=True)}
    assert not any(entry.enabled for entry in busy.values())
    assert busy["photos"].text.endswith("…") and busy["pair"].visible

    _photos, unmounted = photos_states(PhotoProbe(installed=True, device=True))
    assert not presenter.tool_entries([unmounted])[0].visible


def test_mirror_toggle_follows_the_status_key() -> None:
    assert not presenter.mirror_toggle(None).visible
    assert not presenter.mirror_toggle({}).visible  # older backend
    on = presenter.mirror_toggle({"mirror_iphone_removals": True})
    assert (on.visible, on.enabled, on.checked) == (True, True, True)
    assert presenter.mirror_toggle({"mirror_iphone_removals": False}).checked is False


def test_reconnect_entry_follows_the_classic_state() -> None:
    from blueferry.qt import tray_presenter as presenter

    assert presenter.reconnect_entry(None).visible is False
    down = presenter.reconnect_entry({
        "daemon": True, "phone_reconnect_state": "unreachable",
        "phone_reconnect_paused": True, "phone_reconnect_next_in_sec": 300,
    })
    assert down.visible and down.enabled and down.text == "Reconnect iPhone"
    assert presenter.reconnect_entry({
        "daemon": True, "phone_reconnect_state": "connected",
    }).visible is False


def test_tooltip_names_an_unresponsive_bluez() -> None:
    from blueferry.models import BackendStatus
    from blueferry.qt import tray_presenter as presenter
    from blueferry.reconnect_view import BLUEZ_HUNG_TEXT

    status = BackendStatus.from_dict({
        "daemon": True, "bluez_unresponsive": True, "bluez_unresponsive_reason": "kernel",
    })
    assert BLUEZ_HUNG_TEXT in presenter.tooltip(status, 0)


_SHARE_SMOKE = textwrap.dedent("""
    import time
    from PySide6.QtDBus import QDBusConnection
    from PySide6.QtWidgets import QApplication
    from blueferry import plugin_surfaces as surfaces
    from blueferry.qt.tray import TrayController

    app = QApplication([])
    sent, reported = [], []
    choice = surfaces.ShareChoice("io.example.ls", "LocalSend", "iphone", "iPhone (LocalSend)",
                                  "smartphone")
    tray = TrayController(
        QDBusConnection("not-connected"), launch=lambda: True,
        load_targets=lambda: surfaces.ShareTargets([choice]),
        share_available=lambda: True,
        send=lambda target, paths: sent.append((target.key, paths))
        or surfaces.Outcome(True, "Sending 1 file"),
        pick_files=lambda label: ["/tmp/a.jpg"],
    )
    tray.tray.showMessage = lambda title, message, *rest: reported.append(message)
    sections = [a.text() for a in tray.menu.actions() if a.isSeparator() and a.text()]
    assert sections == ["Quick Settings", "Tools"], sections
    assert not tray.share_action.isVisible()
    tray.check_share_available()  # manifests only: nothing is started
    deadline = time.monotonic() + 5
    while not tray.share_action.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert tray.share_action.isVisible()
    tray.refresh_share_targets()
    while (not tray.share_menu.actions() or not tray.share_menu.actions()[0].isEnabled()) \
            and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert [a.text() for a in tray.share_menu.actions()] == ["iPhone (LocalSend)"]
    tray.share_menu.actions()[0].trigger()
    while not reported and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert sent == [("io.example.ls:iphone", ["/tmp/a.jpg"])], sent
    assert reported == ["Sending 1 file"], reported

    def broken(target, paths):
        raise RuntimeError("plugin exploded")

    tray._send = broken
    tray.share_menu.actions()[0].trigger()
    while len(reported) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert reported[1] == "plugin exploded", reported
    tray.close()
    print("SHARE-OK")
""")


def test_tray_groups_its_menu_and_offers_send_to() -> None:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT / "src"),
        "LANGUAGE": "C",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/blueferry-tests-no-live-bus",
    }
    result = subprocess.run(  # nosec B603 - fixed interpreter and script
        [sys.executable, "-c", _SHARE_SMOKE],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert "SHARE-OK" in result.stdout, result.stdout + result.stderr
