"""Hermetic tests for routing the iPhone's A2DP playback (fake bus, fake timers)."""
from __future__ import annotations

import dbus.exceptions
import pytest

from blueferry.backend_operations import BackendDependencies, BackendOperations
from blueferry.cli_audio import describe_route
from blueferry.errors import InvalidArgumentsError, NotReadyError, OperationFailedError
from blueferry.phone_audio_route import (
    A2DP_SINK_UUID,
    A2DP_SOURCE_UUID,
    PhoneAudioRoute,
)

DEV = "/org/bluez/hci0/dev_AA"
FD = f"{DEV}/fd0"


class Timers:
    def __init__(self) -> None:
        self.pending: dict[int, tuple[int, object]] = {}
        self._next = 0

    def schedule(self, seconds, callback) -> int:
        self._next += 1
        self.pending[self._next] = (seconds, callback)
        return self._next

    def cancel(self, source_id) -> None:
        self.pending.pop(source_id, None)

    def fire(self) -> None:
        for source_id, (_seconds, callback) in list(self.pending.items()):
            self.pending.pop(source_id, None)
            callback()


class Bus:
    def __init__(self, tree=None) -> None:
        self.tree = tree if tree is not None else {}
        self.calls: list[tuple] = []
        self.held: list[tuple] = []
        self.receivers: dict[str, object] = {}
        self.removed = 0

    def call_async(self, bus_name, path, interface, method, signature, args,
                   reply_handler, error_handler, timeout=-1.0):
        self.calls.append((path, interface, method, args))
        if method == "GetManagedObjects":
            reply_handler(self.tree)
        else:
            self.held.append((reply_handler, error_handler))

    def add_signal_receiver(self, handler, signal_name=None, **kwargs):
        self.receivers[signal_name] = handler
        bus = self

        class Match:
            def remove(self) -> None:
                bus.removed += 1

        return Match()


def tree(*, connected=True, source=True, transport=False):
    uuids = [A2DP_SOURCE_UUID.upper()] if source else ["0000110e-0000-1000-8000-00805f9b34fb"]
    objects = {DEV: {"org.bluez.Device1": {"Connected": connected, "UUIDs": uuids}}}
    if transport:
        objects[FD] = {"org.bluez.MediaTransport1": {
            "Device": DEV, "UUID": A2DP_SINK_UUID, "State": "idle",
        }}
    return objects


def route(bus, *, allowed=True):
    timers = Timers()
    changes: list[int] = []
    subject = PhoneAudioRoute(
        lambda: bus, DEV, allowed=allowed, on_changed=lambda: changes.append(1),
        schedule=timers.schedule, cancel=timers.cancel,
    )
    return subject, timers, changes


def test_policy_forbids_the_sink_role_so_route_is_unavailable_and_inert() -> None:
    bus = Bus(tree(transport=True))
    subject, _timers, _changes = route(bus, allowed=False)
    subject.start()
    assert bus.calls == [] and bus.receivers == {}
    assert subject.snapshot() == {
        "phone_audio_route": "unavailable",
        "phone_audio_reason": "keep_phone_audio_on_phone",
        "phone_audio_pending": "",
    }
    with pytest.raises(NotReadyError, match="KEEP_PHONE_AUDIO_ON_PHONE=false"):
        subject.set_route("pc", lambda _r: None, lambda _e: None)


@pytest.mark.parametrize(("objects", "expected"), [
    (tree(transport=True), ("pc", "")),
    (tree(), ("phone", "")),
    (tree(connected=False), ("unavailable", "phone_disconnected")),
    (tree(source=False), ("unavailable", "no_a2dp_source")),
    ({}, ("unavailable", "phone_disconnected")),
])
def test_probe_derives_route_from_device_and_transport(objects, expected) -> None:
    subject, _timers, _changes = route(Bus(objects))
    subject.start()
    snap = subject.snapshot()
    assert (snap["phone_audio_route"], snap["phone_audio_reason"]) == expected


def test_transport_of_another_device_or_role_is_ignored() -> None:
    objects = tree()
    objects["/org/bluez/hci0/dev_BB/fd1"] = {"org.bluez.MediaTransport1": {
        "Device": "/org/bluez/hci0/dev_BB", "UUID": A2DP_SINK_UUID}}
    objects[f"{DEV}/fd2"] = {"org.bluez.MediaTransport1": {
        "Device": DEV, "UUID": "0000110a-0000-1000-8000-00805f9b34fb"}}
    subject, _timers, _changes = route(Bus(objects))
    subject.start()
    assert subject.snapshot()["phone_audio_route"] == "phone"


def test_signals_follow_transport_and_disconnects() -> None:
    bus = Bus(tree())
    subject, _timers, changes = route(bus)
    subject.start()
    changes.clear()
    bus.receivers["InterfacesAdded"](FD, {"org.bluez.MediaTransport1": {
        "Device": DEV, "UUID": A2DP_SINK_UUID}})
    assert subject.snapshot()["phone_audio_route"] == "pc"
    bus.receivers["InterfacesRemoved"](FD, ["org.bluez.MediaTransport1"])
    assert subject.snapshot()["phone_audio_route"] == "phone"
    bus.receivers["PropertiesChanged"]("org.bluez.Device1", {"Connected": False}, [])
    assert subject.snapshot()["phone_audio_reason"] == "phone_disconnected"
    bus.receivers["PropertiesChanged"]("org.bluez.Device1", {"RSSI": -40}, [])
    assert len(changes) == 3
    subject.stop()
    assert bus.removed == 3


def test_switch_to_pc_calls_connect_profile_and_settles_with_a_reprobe() -> None:
    bus = Bus(tree())
    subject, timers, _changes = route(bus)
    subject.start()
    accepted: list[str] = []
    subject.set_route("pc", accepted.append, pytest.fail)
    assert bus.calls[-1] == (DEV, "org.bluez.Device1", "ConnectProfile", (A2DP_SOURCE_UUID,))
    assert subject.snapshot()["phone_audio_pending"] == "pc"
    with pytest.raises(NotReadyError, match="in progress"):
        subject.set_route("phone", accepted.append, pytest.fail)
    bus.tree = tree(transport=True)
    bus.held.pop()[0]()
    assert accepted == ["pc"]
    assert subject.snapshot()["phone_audio_pending"] == ""
    assert len(timers.pending) == 1
    timers.fire()
    assert bus.calls[-1][2] == "GetManagedObjects"
    assert subject.snapshot()["phone_audio_route"] == "pc"


def test_switch_to_phone_treats_not_connected_as_success() -> None:
    bus = Bus(tree(transport=True))
    subject, _timers, _changes = route(bus)
    subject.start()
    accepted: list[str] = []
    subject.set_route("phone", accepted.append, pytest.fail)
    assert bus.calls[-1][2] == "DisconnectProfile"
    bus.held.pop()[1](dbus.exceptions.DBusException(
        "x", name="org.bluez.Error.NotConnected"))
    assert accepted == ["phone"]


def test_failure_is_reported_and_clears_pending() -> None:
    bus = Bus(tree())
    subject, _timers, _changes = route(bus)
    subject.start()
    errors: list[Exception] = []
    subject.set_route("pc", pytest.fail, errors.append)
    bus.held.pop()[1](dbus.exceptions.DBusException("x", name="org.bluez.Error.Failed"))
    assert len(errors) == 1
    assert subject.snapshot()["phone_audio_pending"] == ""


def test_invalid_route_and_unavailable_route_are_rejected() -> None:
    subject, _timers, _changes = route(Bus(tree(connected=False)))
    subject.start()
    with pytest.raises(InvalidArgumentsError):
        subject.set_route("speaker", pytest.fail, pytest.fail)
    with pytest.raises(NotReadyError):
        subject.set_route("pc", pytest.fail, pytest.fail)


def test_backend_operations_wraps_failures_as_audio_route_failed() -> None:
    class Audio:
        def snapshot(self):
            return {}

        def set_route(self, route, success, failure):
            failure(RuntimeError("boom"))

    seen: list[Exception] = []
    ops = BackendOperations(object(), BackendDependencies(phone_audio=Audio()))  # type: ignore[arg-type]
    ops.set_phone_audio_route("pc", pytest.fail, seen.append)
    assert isinstance(seen[0], OperationFailedError)
    assert seen[0].dbus_suffix == "AudioRouteFailed"
    with pytest.raises(NotReadyError):
        BackendOperations(object()).set_phone_audio_route(  # type: ignore[arg-type]
            "pc", pytest.fail, pytest.fail)


def test_cli_describes_every_state_without_content() -> None:
    assert "computer" in describe_route({"phone_audio_route": "pc"})
    assert "iPhone" in describe_route({"phone_audio_route": "phone"})
    assert "Switching" in describe_route({"phone_audio_pending": "pc"})
    assert "KEEP_PHONE_AUDIO_ON_PHONE=false" in describe_route({
        "phone_audio_route": "unavailable",
        "phone_audio_reason": "keep_phone_audio_on_phone",
    })
    assert "Update" in describe_route({})


def test_late_reply_after_stop_touches_no_timer_or_bus() -> None:
    bus = Bus(tree())
    subject, timers, changes = route(bus)
    subject.start()
    accepted: list[str] = []
    subject.set_route("pc", accepted.append, pytest.fail)
    reply = bus.held.pop()[0]
    subject.stop()
    assert subject.snapshot()["phone_audio_pending"] == ""
    calls, emitted = len(bus.calls), len(changes)
    reply()
    assert timers.pending == {}
    assert len(bus.calls) == calls and len(changes) == emitted


def test_in_progress_maps_to_not_ready_with_a_retry_hint() -> None:
    bus = Bus(tree())
    subject, _timers, _changes = route(bus)
    subject.start()
    errors: list[Exception] = []
    subject.set_route("pc", pytest.fail, errors.append)
    bus.held.pop()[1](dbus.exceptions.DBusException("x", name="org.bluez.Error.InProgress"))
    assert isinstance(errors[0], NotReadyError) and "try again" in str(errors[0])

    class Audio:
        def snapshot(self):
            return {}

        def set_route(self, route, success, failure):
            failure(NotReadyError("busy"))

    seen: list[Exception] = []
    ops = BackendOperations(object(), BackendDependencies(phone_audio=Audio()))  # type: ignore[arg-type]
    ops.set_phone_audio_route("pc", pytest.fail, seen.append)
    assert isinstance(seen[0], NotReadyError)
