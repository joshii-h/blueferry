"""Opt-in memory-only app-notification log and its D-Bus-facing operation."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from blueferry.ancs.constants import MESSAGES_APP_ID
from blueferry.ancs.events import AncsEvent
from blueferry.backend_operations import BackendDependencies, BackendOperations
from blueferry.cli_notifications import render_notifications
from blueferry.dbus_security import _RULES
from blueferry.errors import NotReadyError
from blueferry.notification_log import NotificationLog

WHEN = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def event(uid: int, app_id: str = "com.example.chat", body: str = "secret") -> AncsEvent:
    return AncsEvent(uid, app_id, "Chat", "Title", "", body, seen_at=WHEN)


def test_log_skips_messages_hides_content_and_signals() -> None:
    changes: list[int] = []
    log = NotificationLog(show_content=False, on_changed=lambda: changes.append(1))
    log.handle_ancs(event(1, MESSAGES_APP_ID))
    log.handle_ancs(event(2))
    assert changes == [1]
    assert log.snapshot(10) == [{
        "id": 2, "app_id": "com.example.chat", "app_name": "Chat",
        "time": WHEN.isoformat(),
    }]


def test_log_keeps_content_when_allowed_is_bounded_and_newest_first() -> None:
    log = NotificationLog(show_content=True, capacity=2)
    for uid in (1, 2, 3):
        log.handle_ancs(event(uid, body="x" * 2000))
    snap = log.snapshot(10)
    assert [record["id"] for record in snap] == [3, 2]
    assert snap[0]["title"] == "Title"
    assert len(str(snap[0]["body"])) == 512
    assert log.snapshot(1)[0]["id"] == 3
    log.clear()
    assert log.snapshot(10) == []


def ops(log=None, *, readable=True, content=False) -> BackendOperations:
    storage = SimpleNamespace(status=SimpleNamespace(can_read=readable, detail="locked"))
    return BackendOperations(object(), BackendDependencies(  # type: ignore[arg-type]
        notification_log=log, notification_content=content, storage=storage,  # type: ignore[arg-type]
    ))


def test_list_notifications_is_disabled_without_opt_in() -> None:
    assert ops().list_notifications(5) == {
        "enabled": False, "content": False, "notifications": [],
    }


def test_list_notifications_fails_closed_while_storage_is_locked() -> None:
    log = NotificationLog(show_content=True)
    log.handle_ancs(event(1))
    with pytest.raises(NotReadyError):
        ops(log, readable=False).list_notifications(5)


def test_list_notifications_bounds_limit_and_clear_history_empties_it(monkeypatch) -> None:
    log = NotificationLog(show_content=True)
    for uid in range(5):
        log.handle_ancs(event(uid))
    operations = ops(log, content=True)
    result = operations.list_notifications(0)
    assert result["enabled"] is True and result["content"] is True
    assert len(result["notifications"]) == 1  # type: ignore[arg-type]
    monkeypatch.setattr("blueferry.backend_operations.clear_events", lambda: None)
    monkeypatch.setattr(operations, "_clear_call_history", lambda: None)
    operations.clear_history(True)
    assert operations.list_notifications(10)["notifications"] == []


def test_notifications_read_has_its_own_bucket() -> None:
    assert "notifications-read" in _RULES


def test_cli_rendering_explains_the_opt_in_and_escapes_text() -> None:
    assert "BLUEFERRY_NOTIFICATION_HISTORY=true" in render_notifications({"enabled": False})[0]
    lines = render_notifications({"enabled": True, "notifications": [
        {"time": WHEN.isoformat(), "app_name": "Chat", "title": "a\x1b[2J", "body": "b"},
    ]})
    assert "\x1b" not in lines[0] and "Chat" in lines[0]


def test_modified_notification_replaces_the_record_with_the_same_id() -> None:
    log = NotificationLog(show_content=True)
    log.handle_ancs(event(1, body="first"))
    log.handle_ancs(event(2))
    log.handle_ancs(event(1, body="edited"))
    snap = log.snapshot(10)
    assert [record["id"] for record in snap] == [2, 1]
    assert snap[1]["body"] == "edited"
    # The same uid from another app is a different notification.
    log.handle_ancs(event(1, app_id="com.example.other"))
    assert len(log.snapshot(10)) == 3


def test_change_signals_are_coalesced_through_the_idle_scheduler() -> None:
    queued: list = []
    changes: list[int] = []
    log = NotificationLog(
        show_content=False, on_changed=lambda: changes.append(1), idle=queued.append,
    )
    for uid in range(3):
        log.handle_ancs(event(uid))
    assert changes == [] and len(queued) == 1
    assert queued.pop()() is False
    assert changes == [1]
    log.clear()
    queued.pop()()
    assert changes == [1, 1]


class _Loop:
    """Inert idle/timeout scheduler: callbacks run only when told to."""

    def __init__(self) -> None:
        self.idle: list = []
        self.timers: dict[int, tuple[int, object]] = {}
        self.next_id = 1

    def add_idle(self, callback) -> int:
        self.idle.append(callback)
        return len(self.idle)

    def schedule(self, seconds: int, callback) -> int:
        source = self.next_id
        self.next_id += 1
        self.timers[source] = (seconds, callback)
        return source

    def cancel(self, source: int) -> None:
        self.timers.pop(source, None)

    def run_idle(self) -> None:
        pending, self.idle = self.idle, []
        for callback in pending:
            callback()

    def run_timers(self) -> None:
        pending, self.timers = self.timers, {}
        for _seconds, callback in pending.values():
            callback()


def _mirrored(mirror: list[bool]) -> tuple[NotificationLog, _Loop, list[int]]:
    loop = _Loop()
    changes: list[int] = []
    log = NotificationLog(
        show_content=False, on_changed=lambda: changes.append(1), idle=loop.add_idle,
        mirror_removals=lambda: mirror[0], schedule=loop.schedule, cancel=loop.cancel,
    )
    return log, loop, changes


def _ids(log: NotificationLog) -> list[int]:
    return [record["id"] for record in log.snapshot(50)]


def test_removal_on_the_iphone_drops_the_record_only_when_mirroring() -> None:
    mirror = [True]
    log, loop, changes = _mirrored(mirror)
    for uid in (1, 2):
        log.handle_ancs(event(uid))
    loop.run_idle()
    changes.clear()
    log.remove(1)
    loop.run_idle()
    assert _ids(log) == [2] and changes == [1]

    mirror[0] = False
    log.remove(2)
    loop.run_idle()
    assert _ids(log) == [2] and changes == [1]  # plain recent history
    log.remove(99)  # unknown UID: nothing, no signal
    assert loop.idle == []


def test_clear_all_on_the_iphone_sends_one_signal() -> None:
    log, loop, changes = _mirrored([True])
    for uid in range(1, 31):
        log.handle_ancs(event(uid))
    loop.run_idle()
    changes.clear()
    for uid in range(1, 31):
        log.remove(uid)
    loop.run_idle()
    assert _ids(log) == [] and changes == [1]


def test_reconnect_keeps_what_the_iphone_reports_again_and_drops_the_rest() -> None:
    log, loop, changes = _mirrored([True])
    for uid in (1, 2, 3):
        log.handle_ancs(event(uid))
    loop.run_idle()
    changes.clear()
    log.session_reset()
    log.session_reset()  # BlueZ often tears down twice
    log.session_ready()
    assert next(iter(loop.timers.values()))[0] == 10  # settle window
    log.preexisting(1)
    log.preexisting(3)
    assert _ids(log) == [3, 2, 1]  # nothing dropped before the window ends
    loop.run_timers()
    loop.run_idle()
    assert _ids(log) == [3, 1] and changes == [1]
    # Adopted records belong to the new session: removals match them now.
    log.remove(3)
    assert _ids(log) == [1]


def test_reconcile_waits_for_a_new_session_and_is_cancelled_by_another_reset() -> None:
    log, loop, _changes = _mirrored([True])
    log.handle_ancs(event(1))
    log.session_reset()
    log.session_ready()
    log.session_reset()  # dropped again before the window ended
    assert loop.timers == {}
    log.session_ready()
    loop.run_timers()
    assert _ids(log) == []
    log.close()


def test_a_reused_uid_never_removes_a_record_of_an_older_session() -> None:
    log, _loop, _changes = _mirrored([True])
    log.handle_ancs(event(7, "com.example.mail"))
    log.session_reset()
    log.session_ready()
    # The new session hands UID 7 to another notification and removes it.
    log.handle_ancs(event(7, "com.example.chat"))
    log.remove(7)
    assert [r["app_id"] for r in log.snapshot(10)] == ["com.example.mail"]
    assert all("_session" not in record for record in log.snapshot(10))


def test_reconnect_without_mirroring_keeps_everything() -> None:
    log, loop, _changes = _mirrored([False])
    log.handle_ancs(event(1))
    log.session_reset()
    log.session_ready()
    loop.run_timers()
    assert _ids(log) == [1]
