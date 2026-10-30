"""Calls1.DeclineWithMessage and SetCallQuickReplies over a private test bus."""
from __future__ import annotations

import itertools
import json
import os

import dbus
import dbus.service
import pytest

from blueferry.backend_operations import BackendDependencies
from blueferry.dbus_service import MessagesService
from blueferry.protocol import BUS_NAME, CALLS_IFACE, MESSAGES_IFACE
from tests.test_calls_dbus import _call
from tests.test_quick_replies import FakeCalls, FakeReplies, Obex, _Sessions

pytestmark = pytest.mark.private_dbus
_ids = itertools.count()


class Guard:
    """Records which rate buckets a call was charged to."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    def authorize(self, _sender, action) -> None:
        self.actions.append(action)

    def forget(self, _sender) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def service():
    bus = dbus.SessionBus()
    name = f"{BUS_NAME}.Replyp{os.getpid()}n{next(_ids)}"
    bus_name = dbus.service.BusName(name, bus=bus, do_not_queue=True)
    calls, replies, obex, guard = FakeCalls(), FakeReplies(), Obex(), Guard()
    instance = MessagesService(
        bus_name, _Sessions(),
        BackendDependencies(
            status_provider=lambda: {"initializing": False},
            calls=calls, quick_replies=replies, submit_obex=obex,
        ),
        caller_guard=guard,
    )
    try:
        yield name, calls, replies, obex, guard
    finally:
        instance.close()
        instance.remove_from_connection()
        bus.release_name(name)


def test_decline_with_message_counts_as_call_control_and_a_send(service) -> None:
    name, calls, _replies, obex, guard = service

    outcome = _call(name, CALLS_IFACE, "DeclineWithMessage", "voicecall01", "Later")

    assert "error" not in outcome
    assert calls.requests == [("hangup", "voicecall01")] and len(obex.sent) == 1
    assert guard.actions[-2:] == ["calls-control", "send"]


def test_decline_with_message_errors_are_typed(service) -> None:
    name, calls, _replies, obex, _guard = service
    calls.number = ""
    unknown = _call(name, CALLS_IFACE, "DeclineWithMessage", "voicecall01", "Later")["error"]
    calls.number = "+41791234567"
    obex.error = RuntimeError("transfer failed")
    not_sent = _call(name, CALLS_IFACE, "DeclineWithMessage", "voicecall01", "Later")["error"]

    assert unknown.get_dbus_name() == "io.weirdware.BlueFerry.Error.InvalidArgs"
    assert not_sent.get_dbus_name() == "io.weirdware.BlueFerry.Error.DeclinedNotSent"
    assert "declined" in not_sent.get_dbus_message()
    assert calls.requests == [("hangup", "voicecall01")]  # only the second call declined


def test_quick_replies_round_trip_through_get_status(service) -> None:
    name, _calls, _replies, _obex, guard = service

    saved = json.loads(_call(name, CALLS_IFACE, "SetCallQuickReplies",
                             dbus.Array(["A", "B"], signature="s"))["value"])
    invalid = _call(name, CALLS_IFACE, "SetCallQuickReplies",
                    dbus.Array(["A", "A"], signature="s"))["error"]
    status = json.loads(_call(name, MESSAGES_IFACE, "GetStatus")["value"])

    assert saved == {"calls_quick_replies": ["A", "B"]}
    assert invalid.get_dbus_name() == "io.weirdware.BlueFerry.Error.InvalidArgs"
    assert status["calls_quick_replies"] == ["A", "B"]
    assert "settings" in guard.actions
