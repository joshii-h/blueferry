"""Battery Service GATT client against the inert fake BlueZ of the AMS tests."""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from blueferry.battery_service import (
    BATTERY_LEVEL_CHAR,
    BatteryServiceClient,
    parse_battery_level,
)
from tests.test_ams_client import GATT, _Bus, _Timers

DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
LEVEL = f"{DEVICE}/service000f/char0010"


def _objects(device: str = DEVICE) -> dict:
    return {
        LEVEL.replace(DEVICE, device): {GATT: {"UUID": BATTERY_LEVEL_CHAR.upper()}},
        f"{device}/service0040/char0041": {GATT: {"UUID": "0000aaaa-0000-1000-8000-00805f9b34fb"}},
    }


@pytest.fixture
def harness():
    bus = _Bus()
    timers = _Timers()
    levels: list[int | None] = []
    client = BatteryServiceClient(
        DEVICE,
        on_level=levels.append,
        bus_factory=lambda: bus,
        schedule=timers.schedule,
        cancel=timers.cancel,
    )
    yield client, bus, timers, levels
    client.stop()


def _subscribe(client, bus, timers, first: bytes = b"\x57") -> None:
    client.observe_bearer_state(True)
    client.start()
    bus.take("GetManagedObjects").succeed(_objects())
    timers.run_all()
    bus.take("ReadValue", LEVEL).succeed(list(first))
    bus.take("StartNotify", LEVEL).succeed()


def test_reads_once_then_subscribes_after_the_bearer_settles(harness) -> None:
    client, bus, timers, levels = harness
    client.observe_bearer_state(True)
    client.start()
    bus.take("GetManagedObjects").succeed(_objects())
    assert bus.pending() == []  # nothing before the LE link settles
    timers.run_all()
    assert bus.pending() == [("ReadValue", LEVEL)]
    bus.take("ReadValue", LEVEL).succeed([87])
    assert client.level == 87
    assert levels == [87]
    bus.take("StartNotify", LEVEL).succeed()
    bus.notify(LEVEL, b"\x56")
    assert levels == [87, 86]
    assert not any(call.method == "StopNotify" for call in bus.calls)


def test_ignores_other_devices_and_waits_for_the_characteristic(harness) -> None:
    client, bus, timers, _levels = harness
    client.observe_bearer_state(True)
    client.start()
    bus.take("GetManagedObjects").succeed(_objects("/org/bluez/hci0/dev_11_22_33_44_55_66"))
    timers.run_all()
    assert bus.pending() == []
    client._on_iface_added(LEVEL, {GATT: {"UUID": BATTERY_LEVEL_CHAR}})
    assert bus.pending() == [("ReadValue", LEVEL)]


def test_bearer_loss_clears_the_level_and_discards_late_replies(harness) -> None:
    client, bus, timers, levels = harness
    client.observe_bearer_state(True)
    client.start()
    bus.take("GetManagedObjects").succeed(_objects())
    timers.run_all()
    read = bus.take("ReadValue", LEVEL)
    client.observe_bearer_state(False)
    read.succeed([50])
    assert client.level is None
    assert levels == []
    assert bus.pending() == []

    client.observe_bearer_state(True)
    timers.run_all()
    bus.take("ReadValue", LEVEL).succeed([60])
    bus.take("StartNotify", LEVEL).succeed()
    client.observe_bearer_state(False)
    assert client.level is None
    assert levels == [60, None]
    bus.notify(LEVEL, b"\x10")  # the watch was removed
    assert client.level is None


def test_reconnect_keeps_a_surviving_notify_registration(harness) -> None:
    client, bus, timers, _levels = harness
    _subscribe(client, bus, timers)
    client.observe_bearer_state(False)
    client.observe_bearer_state(True)
    timers.run_all()
    bus.take("ReadValue", LEVEL).succeed([40])
    bus.take("Get", LEVEL).succeed(True)
    assert bus.pending() == []
    assert client.level == 40


def test_failure_retries_with_backoff(harness) -> None:
    client, bus, timers, _levels = harness
    client.observe_bearer_state(True)
    client.start()
    bus.take("GetManagedObjects").succeed(_objects())
    timers.run_all()
    bus.take("ReadValue", LEVEL).fail("org.bluez.Error.NotPermitted")
    assert list(timers.delays.values())[-1] == 2
    timers.run_all()
    bus.take("ReadValue", LEVEL).fail()
    assert list(timers.delays.values())[-1] == 4
    timers.run_all()
    bus.take("ReadValue", LEVEL).succeed([12])
    bus.take("StartNotify", LEVEL).succeed()
    assert client.level == 12


def test_owner_change_and_removed_characteristic_reset(harness) -> None:
    client, bus, timers, _levels = harness
    _subscribe(client, bus, timers)
    client._on_iface_removed(LEVEL, [GATT])
    assert client.level is None
    assert bus.pending() == []
    client.observe_bluez_owner(":1.1", ":1.2")
    assert bus.pending() == [("GetManagedObjects", "/")]
    assert client.level is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(b"\x00", 0), (b"\x64", 100), (b"\x65", None), (b"", None), (b"\x10\x00", None), (None, None)],
)
def test_parse_battery_level(value, expected) -> None:
    assert parse_battery_level(value) == expected


@given(st.binary(max_size=4))
def test_parse_battery_level_never_raises(value: bytes) -> None:
    level = parse_battery_level(value)
    assert level is None or 0 <= level <= 100
