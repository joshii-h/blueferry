"""Phone strip, media/sound/hotspot keys and the iPhone screen in the TUI."""
from __future__ import annotations

import asyncio
import time

from textual.widgets import Static

from blueferry.models import BackendStatus, CallHistoryEntry
from blueferry.tether_status import TetherStatus
from blueferry.tui import BlueFerryApp, TuiState
from blueferry.tui_phone import PhoneScreen


class _Backend:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.requests: list[tuple] = []
        self.tether = TetherStatus(state="off")

    def status(self) -> BackendStatus:
        extra = {
            "call_history_enabled": self.enabled,
            "notification_history_enabled": self.enabled,
            "media_control_enabled": self.enabled,
        }
        if self.enabled:
            extra |= {
                "phone_audio_route": "phone", "phone_audio_reason": "",
                "proximity_lock": "present", "proximity_lock_enabled": False,
                "proximity_lock_grace_sec": 20, "phone_battery_percent": 57,
                "phone_battery_level": 60, "phone_signal_strength": 80,
            }
        return BackendStatus.from_dict({
            "daemon": True, "map": True, "calls_enabled": self.enabled, **extra,
        })

    def threads(self, limit: int = 1000):
        return []

    def now_playing(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True, "available": True, "player": {"state": "playing"},
            "track": {"title": "[b]Song[/b]", "artist": "Band"},
            "supported_commands": ["toggle", "next", "previous"],
        }

    def send_media_command(self, command: str) -> None:
        self.requests.append(("media", command))

    def set_phone_audio_route(self, route: str) -> str:
        self.requests.append(("route", route))
        return route

    def tether_state(self) -> TetherStatus:
        return self.tether

    def tether_connect(self) -> TetherStatus:
        self.requests.append(("tether", True))
        return TetherStatus(state="connecting")

    def tether_disconnect(self) -> TetherStatus:
        self.requests.append(("tether", False))
        return TetherStatus(state="disconnecting")

    def set_proximity_lock(self, enabled: bool, grace_seconds: int) -> dict:
        self.requests.append(("lock", enabled, grace_seconds))
        return {}

    def call_history(self, limit: int = 200) -> list[CallHistoryEntry]:
        entry = CallHistoryEntry.from_dict({
            "direction": "missed", "timestamp": "2026-08-10T10:00:00+00:00",
            "address": "+41790000000", "name": "[red]Ann",
        })
        assert entry is not None
        return [entry]

    def notifications(self, limit: int = 50) -> dict:
        return {"notifications": [
            {"app_name": "Mail", "title": "[i]Hello", "body": "\x1b[2Jbody",
             "time": "2026-08-10T10:00:00+00:00"},
        ]}


def _run(coroutine) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def _until(pilot, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        await pilot.pause(0.05)
    assert predicate()


def _plain(app, selector: str) -> str:
    return app.screen.query_one(selector, Static).render().plain


def test_strip_shows_phone_status_and_escaped_now_playing_and_keys_act() -> None:
    async def scenario() -> None:
        backend = _Backend()
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 36)) as pilot:
            await _until(pilot, lambda: "Song" in _plain(app, "#now-playing"))
            assert "[b]Song[/b] — Band" in _plain(app, "#now-playing")
            assert "Battery 57 %" in _plain(app, "#phone-status")
            app.set_focus(None)
            for key in ("p", "right_square_bracket", "left_square_bracket", "a"):
                await pilot.press(key)
            await _until(pilot, lambda: len(backend.requests) == 4)
            assert backend.requests == [
                ("media", "toggle"), ("media", "next"), ("media", "previous"),
                ("route", "pc"),
            ]
            await pilot.press("t")
            await _until(pilot, lambda: ("tether", True) in backend.requests)

    _run(scenario())


def test_phone_screen_lists_calls_and_notifications_as_plain_text() -> None:
    async def scenario() -> None:
        backend = _Backend()
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 44)) as pilot:
            await _until(pilot, lambda: app.state.status.calls_enabled)
            app.set_focus(None)
            await pilot.press("o")
            await _until(pilot, lambda: isinstance(app.screen, PhoneScreen))
            await _until(pilot, lambda: "Ann" in _plain(app, "#phone-calls"))
            assert "[red]Ann" in _plain(app, "#phone-calls")
            notes = _plain(app, "#phone-notifications")
            assert "[i]Hello" in notes and "\x1b" not in notes
            assert "Lock when the iPhone goes away" in _plain(app, "#phone-switches")
            await pilot.press("l")
            await _until(pilot, lambda: ("lock", True, 20) in backend.requests)
            await pilot.press("escape")
            await _until(pilot, lambda: not isinstance(app.screen, PhoneScreen))

    _run(scenario())


def test_disabled_features_show_opt_in_hints_and_send_nothing() -> None:
    async def scenario() -> None:
        backend = _Backend(enabled=False)
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(220, 44)) as pilot:
            await _until(
                pilot, lambda: "BLUEFERRY_MEDIA_CONTROL_ENABLED" in _plain(app, "#now-playing"),
            )
            assert "BLUEFERRY_CALLS_ENABLED" in _plain(app, "#phone-status")
            app.set_focus(None)
            for key in ("p", "a"):
                await pilot.press(key)
            await pilot.press("o")
            await _until(pilot, lambda: isinstance(app.screen, PhoneScreen))
            assert "BLUEFERRY_CALL_HISTORY_ENABLED" in _plain(app, "#phone-calls")
            assert "BLUEFERRY_NOTIFICATION_HISTORY" in _plain(app, "#phone-notifications")
            await pilot.press("l")
            await pilot.pause(0.2)
            assert backend.requests == []

    _run(scenario())
