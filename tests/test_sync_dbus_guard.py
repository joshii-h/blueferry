"""The conftest guard reports blocking D-Bus calls made by daemon code."""
from __future__ import annotations

import os
import threading

import pytest

from blueferry import daemon
from tests.private_bus import open_private_bus

pytestmark = pytest.mark.private_dbus

_DAEMON_FILE = os.path.abspath(daemon.__file__)
_SOURCE = """
def probe_bus_id(bus):
    return bus.get_object(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False,
    ).GetId(dbus_interface="org.freedesktop.DBus")
"""


def _daemon_function():
    # Compiled under daemon.py's path, so frames look like daemon code.
    namespace: dict = {}
    exec(compile(_SOURCE, _DAEMON_FILE, "exec"), namespace)
    return namespace["probe_bus_id"]


@pytest.fixture
def bus():
    connection = open_private_bus()
    yield connection
    connection.close()


def test_blocking_call_from_daemon_code_is_reported(bus, sync_dbus_guard) -> None:
    assert _daemon_function()(bus)
    assert sync_dbus_guard == ["blueferry.daemon:probe_bus_id -> GetId"]
    sync_dbus_guard.clear()  # this test made the call on purpose


def test_tests_and_worker_threads_may_block(bus, sync_dbus_guard) -> None:
    assert bus.get_object(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False,
    ).GetId(dbus_interface="org.freedesktop.DBus")
    worker = threading.Thread(target=_daemon_function(), args=(bus,))
    worker.start()
    worker.join()
    assert sync_dbus_guard == []
