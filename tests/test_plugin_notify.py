"""Plugin popups (capability notify): sender checks, policy, rate limit, click.

The bus and the notification service are recording fakes; no session bus is
opened and no plugin runs.
"""
from __future__ import annotations

import json

import pytest

from blueferry import config
from blueferry.event_dispatcher import EventDispatcher
from blueferry.plugin_api.surfaces import Notification
from blueferry.plugin_api.testing import manifest
from blueferry.plugin_notify import POPUPS_PER_MINUTE, PluginPopup, PluginPopups
from blueferry.sinks.libnotify import LibnotifySink

CALENDAR = manifest("io.example.calendar", capabilities="notify;", api_version="1.2")
OTHER = manifest("io.example.other", capabilities="notify;", api_version="1.2")


class _Bus:
    def __init__(self, owners=None, uids=None) -> None:
        self.owners = owners or {}
        self.uids = uids or {}
        self.receivers = []
        self.calls = []
        self.default_uid = 1000

    def add_signal_receiver(self, callback, **kwargs):
        self.receivers.append((callback, kwargs))
        return self

    def remove(self) -> None:
        self.receivers.clear()

    def call_async(self, bus_name, path, interface, method, signature, args,
                   reply, error, timeout=-1.0):
        self.calls.append((bus_name, interface, method, tuple(args)))
        if method == "GetNameOwner":
            owner = self.owners.get(args[0])
            return reply(owner) if owner else error(RuntimeError("no owner"))
        if method == "GetConnectionUnixUser":
            return reply(self.uids.get(args[0], self.default_uid))
        if method == "InvokeAction":
            return self.invoke(reply, error)
        raise AssertionError(method)


def _popups(bus, shown, **kwargs):
    popups = PluginPopups(bus, show=shown.append, plugins=lambda: [OTHER, CALENDAR],
                          uid=1000, **kwargs)
    popups.start()
    return popups


def _emit(bus, *args, sender=":1.7"):
    callback, kwargs = bus.receivers[0]
    callback(*args, **{kwargs["sender_keyword"]: sender})


def test_only_the_owner_of_an_enabled_notify_plugin_gets_a_popup() -> None:
    bus = _Bus(owners={CALENDAR.bus_name: ":1.7", OTHER.bus_name: ":1.8"},
               uids={":1.7": 1000, ":1.9": 1000})
    shown: list[PluginPopup] = []
    _popups(bus, shown)
    assert bus.receivers[0][1]["dbus_interface"] == "io.weirdware.BlueFerry.Plugin1"

    _emit(bus, "Dentist", "10:00 \x1b[2J", "view-calendar", "Open", "open")
    _emit(bus, "Spoof", "from a stranger", "", "", "", sender=":1.9")
    _emit(bus, "", "", "", "", "")
    assert [(p.plugin_id, p.note.title, p.note.body) for p in shown] == [
        (CALENDAR.id, "Dentist", "10:00 [2J")]
    assert shown[0].note.has_action


def test_a_plugin_run_by_another_user_is_ignored() -> None:
    bus = _Bus(owners={CALENDAR.bus_name: ":1.7"}, uids={":1.7": 0})
    shown: list[PluginPopup] = []
    _popups(bus, shown)
    _emit(bus, "Hi", "there")
    assert shown == []


def test_each_plugin_gets_a_few_popups_a_minute() -> None:
    now = [0.0]
    bus = _Bus(owners={CALENDAR.bus_name: ":1.7"})
    shown: list[PluginPopup] = []
    _popups(bus, shown, clock=lambda: now[0])
    for _ in range(POPUPS_PER_MINUTE + 3):
        _emit(bus, "Hi", "there")
    assert len(shown) == POPUPS_PER_MINUTE
    now[0] = 61.0
    _emit(bus, "Later", "again")
    assert len(shown) == POPUPS_PER_MINUTE + 1


def test_a_click_invokes_the_plugin_and_opens_only_safe_uris(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    ics = tmp_path / "blueferry" / CALENDAR.id / "a.ics"
    ics.parent.mkdir(parents=True)
    ics.write_text("x")
    import os

    opened: list[str] = []
    bus = _Bus()
    bus.default_uid = os.getuid()
    popups = PluginPopups(bus, show=lambda _p: None, plugins=lambda: [CALENDAR],
                          uid=os.getuid(), open_uri=opened.append)
    popup = PluginPopup(CALENDAR.id, "Calendar", CALENDAR.bus_name,
                        Notification("T", "B", "", "Open", "open"))
    for uri in (ics.as_uri(), "file:///etc/passwd", "https://example.org/e"):
        bus.invoke = lambda reply, error, uri=uri: reply(json.dumps({"ok": True,
                                                                      "open_uri": uri}))
        popups.invoke(popup)
    bus.invoke = lambda reply, error: reply("{garbage")
    popups.invoke(popup)
    bus.invoke = lambda reply, error: error(RuntimeError("crashed"))
    popups.invoke(popup)
    assert opened == [ics.resolve().as_uri(), "https://example.org/e"]
    invoke = next(call for call in bus.calls if call[2] == "InvokeAction")
    assert invoke == (CALENDAR.bus_name, "io.weirdware.BlueFerry.Plugin1", "InvokeAction",
                      ("notify", "open", "{}"))
    # A popup without a button never calls the plugin.
    count = len(bus.calls)
    popups.invoke(PluginPopup(CALENDAR.id, "Calendar", CALENDAR.bus_name, Notification("T", "B")))
    assert len(bus.calls) == count


class _Notifications:
    def __init__(self) -> None:
        self.calls = []

    def Notify(self, *args, reply_handler=None, error_handler=None):
        # Plugin popups never block the daemon's loop.
        assert reply_handler is not None and error_handler is not None
        self.calls.append(args)
        reply_handler(50 + len(self.calls))


def _sink(policy="messages", on_plugin_action=None):
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notification_policy = lambda: policy
    sink._notif = _Notifications()
    sink._pending = {}
    sink._msg_subs = {}
    sink._plugin_popups = {}
    sink._on_plugin_action = on_plugin_action
    return sink


def _popup(**note) -> PluginPopup:
    values = {"title": "<b>Dentist</b>", "body": "10:00 & more", "icon": "view-calendar",
              "action_label": "Open <i>now</i>", "action_id": "open"}
    values.update(note)
    return PluginPopup(CALENDAR.id, "Calendar", CALENDAR.bus_name, Notification(**values))


@pytest.mark.parametrize("content", [True, False])
def test_sink_escapes_text_and_honours_the_content_switch(monkeypatch, content) -> None:
    monkeypatch.setattr(config, "SHOW_NOTIFICATION_CONTENT", content)
    clicked = []
    sink = _sink(on_plugin_action=clicked.append)
    sink.handle_plugin_notification(_popup())
    _app, _replace, icon, title, body, actions, _hints, _timeout = sink._notif.calls[0]
    assert icon == "view-calendar"
    if content:
        assert title == "&lt;b&gt;Dentist&lt;/b&gt;" and body == "10:00 &amp; more"
    else:
        assert title == "Calendar" and body == "New notification"
    assert list(actions) == ["plugin-action", "Open now" if content else "Open"]
    sink._on_action(51, "plugin-action")
    sink._on_action(51, "plugin-action")  # single use
    assert len(clicked) == 1 and clicked[0].note.action_id == "open"


def test_sink_respects_none_and_closing_never_runs_the_action() -> None:
    silent = _sink(policy="none", on_plugin_action=lambda _p: None)
    silent.handle_plugin_notification(_popup())
    assert silent._notif.calls == []
    clicked = []
    sink = _sink(on_plugin_action=clicked.append)
    sink.handle_plugin_notification(_popup())
    sink.handle_plugin_notification(_popup(action_label="", action_id=""))
    assert list(sink._notif.calls[1][5]) == []
    sink._on_closed(51, 2)
    sink._on_action(51, "plugin-action")
    assert clicked == []


def test_dispatcher_forwards_plugin_popups_and_the_click_hook() -> None:
    seen = []
    kwargs_seen = []

    class _Sink:
        name = "libnotify"

        def __init__(self, **kwargs) -> None:
            kwargs_seen.append(kwargs)

        def handle_plugin_notification(self, popup) -> None:
            seen.append(popup)

    class _NoBus:
        def add_signal_receiver(self, *_args, **_kwargs):
            return self

        def name_has_owner(self, _name):
            return True

    dispatcher = EventDispatcher(
        contacts=None, defer_mark_read=lambda _path: None,
        plugin_action=lambda _popup: None, notification_sink_factory=_Sink,
        otp_autocopy=lambda: False, session_bus=_NoBus(), storage=object(),
    )
    dispatcher._setup_complete = True
    assert dispatcher._ensure_libnotify_sink()
    assert "on_plugin_action" in kwargs_seen[0]
    dispatcher.plugin_notification(_popup())
    assert len(seen) == 1


def test_unverified_senders_are_rate_limited_and_verified_ones_remembered() -> None:
    now = [0.0]
    bus = _Bus(owners={CALENDAR.bus_name: ":1.7"})
    shown: list[PluginPopup] = []
    reads = []

    def plugins():
        reads.append(1)
        return [CALENDAR]

    popups = PluginPopups(bus, show=shown.append, plugins=plugins, uid=1000,
                          clock=lambda: now[0])
    popups.start()
    for _ in range(40):
        _emit(bus, "Spam", "x", sender=":1.66")
    lookups = [call for call in bus.calls if call[2] == "GetNameOwner"]
    assert len(lookups) == 12  # LOOKUPS_PER_MINUTE, then dropped unasked
    assert len(reads) == 1  # manifests cached
    _emit(bus, "Hi", "there")
    asked = len(bus.calls)
    # The plugin idles out; its next popup still counts as verified.
    bus.owners.clear()
    _emit(bus, "Again", "later")
    assert [p.note.title for p in shown] == ["Hi", "Again"]
    assert len(bus.calls) == asked


def test_one_shot_senders_do_not_grow_the_lookup_table() -> None:
    now = [0.0]
    bus = _Bus()
    popups = PluginPopups(bus, show=lambda _p: None, plugins=lambda: [CALENDAR], uid=1000,
                          clock=lambda: now[0])
    popups.start()
    for n in range(300):
        _emit(bus, "x", "y", sender=f":1.{n}")
    now[0] = 120.0
    for n in range(300, 600):
        _emit(bus, "x", "y", sender=f":1.{n}")
    assert len(popups._lookups) <= 300
