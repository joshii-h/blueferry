"""Fail-closed isolation from the user's desktop and paired devices."""
from __future__ import annotations

import atexit
import functools
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import threading

# Before anything imports blueferry.config: its default paths are the
# operator's real configuration, history, and contact cache. Point them at a
# throwaway tree so even a test that undoes its own isolation cannot reach
# private data or load the operator's local.env.
_scratch_home = tempfile.mkdtemp(prefix="blueferry-tests-")
atexit.register(shutil.rmtree, _scratch_home, True)
for _variable, _child in (
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_RUNTIME_DIR", "runtime"),
):
    os.makedirs(os.path.join(_scratch_home, _child), mode=0o700)
    os.environ[_variable] = os.path.join(_scratch_home, _child)

import dbus  # noqa: E402
import dbus.connection  # noqa: E402
import pytest  # noqa: E402
from gi.repository import GLib  # noqa: E402

# Before anything imports blueferry: record every GLib timer and idle source
# armed from Python so the guard below can fail a test that leaves one behind.
# Supervisors bind GLib.timeout_add_seconds as a default argument at import
# time, so the wrappers must already be in place when those modules load.
# A leaked source fires later on an orphaned object inside whichever test
# happens to iterate the default main context, and on the private test bus
# that can stall an unrelated test on a blocking D-Bus call.
# Raise rather than assert so the ordering check survives ``python -O``.
if any(name == "blueferry" or name.startswith("blueferry.") for name in sys.modules):
    raise RuntimeError("GLib source guard must be installed before blueferry is imported")
_glib_sources_armed: list[tuple[int, str, object]] | None = None


def _record_glib_source(name: str):
    original = getattr(GLib, name)
    assert callable(original), f"GLib.{name} is not callable"

    @functools.wraps(original)
    def armed(*args, **kwargs):
        source_id = original(*args, **kwargs)
        record = _glib_sources_armed
        if record is not None:
            callback = next((arg for arg in args if callable(arg)), None)
            record.append((source_id, name, callback))
        return source_id

    setattr(GLib, name, armed)


for _glib_name in ("timeout_add", "timeout_add_seconds", "idle_add"):
    _record_glib_source(_glib_name)

from blueferry import bus as bus_module  # noqa: E402

# conftest is loaded before pytest imports test modules. Poison live bus
# addresses here—not merely in a fixture—so GTK/Gio collection-time probes
# cannot reach the desktop either.
PRIVATE_BUS_ADDRESS_ENV = "BLUEFERRY_TEST_DBUS_ADDRESS"
_active_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
_expected_test_bus = os.environ.get(PRIVATE_BUS_ADDRESS_ENV)
_running_private_suite = bool(
    _active_bus and _active_bus == _expected_test_bus
)
_unreachable_bus = "unix:path=/tmp/blueferry-tests-no-live-bus"
if not _running_private_suite:
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = _unreachable_bus
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = _unreachable_bus

# Qt tests must not adopt the operator's desktop. A platform theme plugin is
# the dangerous one: QT_QPA_PLATFORMTHEME=gtk3 pulls GTK3 into a process where
# the GTK4 client tests have already initialized gi, and the two GLib
# thread-default context stacks deadlock the suite. Force a headless platform
# and no theme plugin before any test module can import PySide6.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# GTK and Qt run in separate processes in production, but these tests load
# both toolkits. With Qt 6.11, GTK initializing GLib before Qt can leave Qt's
# worker dispatcher cleaning up after GLib's thread-local context stack has
# been destroyed. Use Qt's native dispatcher here; tests that need GLib
# dispatch explicitly iterate its context themselves.
os.environ["QT_NO_GLIB"] = "1"
# Package builds can reuse Qt's per-user compiled QML cache even though they
# are testing a newly extracted source tree at the same path. Always compile
# the QML under test from its current source.
os.environ["QML_DISABLE_DISK_CACHE"] = "1"
os.environ.pop("QT_QPA_PLATFORMTHEME", None)
os.environ.pop("QT_STYLE_OVERRIDE", None)


def _forbid_live_bus(kind: str):
    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            f"test attempted to open the real {kind} D-Bus; inject a fake "
            "connection or use the private_dbus marker"
        )

    return forbidden


class GlibSourceGuard:
    """Sources armed through GLib during the current test."""

    def __init__(self) -> None:
        self.armed: list[tuple[int, str, object]] = []

    def live(self) -> list[tuple[int, str, object]]:
        context = GLib.MainContext.default()
        live = []
        for source_id, name, callback in self.armed:
            source = context.find_source_by_id(source_id)
            if source is not None and not source.is_destroyed():
                live.append((source_id, name, callback))
        return live


@pytest.fixture(autouse=True)
def glib_source_guard():
    """Fail a test that leaves a GLib timer or idle source armed.

    Tests inject ``schedule``/``cancel`` fakes into supervisors instead of
    arming real sources. Private D-Bus tests may use real GLib dispatch, but
    everything they arm must have fired or been removed by teardown. This
    fixture is defined first so its teardown runs after every other
    function-scoped fixture has cleaned up.
    """
    global _glib_sources_armed
    guard = GlibSourceGuard()
    _glib_sources_armed = guard.armed
    try:
        yield guard
    finally:
        _glib_sources_armed = None
    leaked = guard.live()
    for source_id, _name, _callback in leaked:
        # Do not let the orphan fire inside a later, unrelated test.
        GLib.source_remove(source_id)
    assert not leaked, (
        "test left GLib sources armed; inject schedule/cancel fakes: "
        + ", ".join(
            f"GLib.{name}({getattr(callback, '__qualname__', None) or repr(callback)})"
            for _id, name, callback in leaked
        )
    )


def _load_mainloop_lint():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "lint_mainloop.py",
    )
    spec = importlib.util.spec_from_file_location("_blueferry_lint_mainloop", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module
    spec.loader.exec_module(module)
    return module


# Synchronous dbus-python method calls from daemon code on the main thread
# stall the GLib loop. tools/lint_mainloop.py finds the literal ones; this
# guard catches what only shows up at run time (a getattr'd method, a helper
# that blocks). Every blocking proxy call ends in Connection.call_blocking,
# so wrap that and attribute it to the first caller outside dbus-python.
# Tests may call synchronously on the private bus; only frames in daemon
# modules count, and the lint's allowlist and known-debt list apply.
_mainloop_lint = _load_mainloop_lint()
_DAEMON_MODULES = frozenset(_mainloop_lint.daemon_modules(_mainloop_lint.load_sources()))
_SYNC_DBUS_EXEMPT = frozenset(
    (module, function)
    for module, function, rule in (
        *_mainloop_lint.ALLOWLIST, *_mainloop_lint.KNOWN_DEBT,
    )
    if rule == "sync-dbus"
)
_PACKAGE_DIR = str(_mainloop_lint.PACKAGE_DIR)
_DBUS_DIR = os.path.dirname(os.path.abspath(dbus.__file__))
_sync_dbus_calls: list[str] | None = None


def sync_dbus_caller(frame) -> str | None:
    """``module:function`` of the daemon code behind a blocking call, if any."""
    while frame is not None and os.path.abspath(frame.f_code.co_filename).startswith(_DBUS_DIR):
        frame = frame.f_back
    if frame is None or threading.current_thread() is not threading.main_thread():
        return None
    filename = os.path.abspath(frame.f_code.co_filename)
    if not filename.startswith(_PACKAGE_DIR + os.sep):
        return None
    module = _mainloop_lint.module_name(pathlib.Path(filename))
    function = frame.f_code.co_qualname.replace(".<locals>", "")
    if module not in _DAEMON_MODULES or (module, function) in _SYNC_DBUS_EXEMPT:
        return None
    return f"{module}:{function}"


# dbus-python itself makes blocking calls to the bus daemon: AddMatch and
# RemoveMatch for add_signal_receiver/match.remove(), GetNameOwner when
# get_object resolves a well-known name. Those round trips are local and
# every proxy and signal watch in the daemon depends on them; they go away
# module by module with the Gio.DBus migration. The same methods called
# directly from daemon code are still reported.
_IMPLICIT_BUS_DAEMON_CALLS = frozenset({"AddMatch", "RemoveMatch", "GetNameOwner"})


def _record_sync_dbus(original):
    @functools.wraps(original)
    def call_blocking(self, bus_name, object_path, dbus_interface, method, *args, **kwargs):
        record = _sync_dbus_calls
        frame = sys._getframe(1)
        if record is not None and not (
            bus_name == "org.freedesktop.DBus"
            and method in _IMPLICIT_BUS_DAEMON_CALLS
            and os.path.abspath(frame.f_code.co_filename).startswith(_DBUS_DIR)
        ):
            caller = sync_dbus_caller(frame)
            if caller is not None:
                record.append(f"{caller} -> {method}")
        return original(self, bus_name, object_path, dbus_interface, method, *args, **kwargs)

    return call_blocking


dbus.connection.Connection.call_blocking = _record_sync_dbus(
    dbus.connection.Connection.call_blocking
)


@pytest.fixture(autouse=True)
def sync_dbus_guard():
    """Fail a test in which daemon code made a blocking D-Bus call.

    Only real connections reach ``call_blocking``, so in practice this covers
    private-bus tests. Fix the call (``blueferry.dbus_call.call_async``) or,
    for worker-thread or CLI code the lint cannot tell apart, add a reasoned
    ``ALLOWLIST`` entry in tools/lint_mainloop.py.
    """
    global _sync_dbus_calls
    calls: list[str] = []
    _sync_dbus_calls = calls
    try:
        yield calls
    finally:
        _sync_dbus_calls = None
    assert not calls, (
        "daemon code made blocking D-Bus calls on the main thread: " + ", ".join(calls)
    )


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _require_private_bus_helper(factory):
    """Reject private connections that test code opens without the helper.

    libdbus exits the process when a closed private connection with
    exit-on-disconnect still set is dispatched, which turns one failing
    private-bus test into a silent pytest crash. ``tests.private_bus``
    disables that; production callers are left untouched.
    """
    @functools.wraps(factory)
    def connect(*args, **kwargs):
        if kwargs.get("private"):
            caller = os.path.abspath(sys._getframe(1).f_code.co_filename)
            if (
                os.path.dirname(caller) == _TESTS_DIR
                and os.path.basename(caller) != "private_bus.py"
            ):
                raise AssertionError(
                    "open private test-bus connections with "
                    "tests.private_bus.open_private_bus"
                )
        return factory(*args, **kwargs)

    return connect


@pytest.fixture(autouse=True)
def isolate_dbus(monkeypatch, request):
    """Make accidental BlueZ, OBEX, daemon, and notification access fatal.

    The integration test is allowed only when its caller records the address
    created by dbus-run-session. Merely having a desktop session bus is never
    enough to opt a test into external I/O.
    """
    if request.node.get_closest_marker("private_dbus") is not None:
        active = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        expected = os.environ.get(PRIVATE_BUS_ADDRESS_ENV)
        if not active or active != expected:
            pytest.skip("requires an explicitly isolated dbus-run-session")
        monkeypatch.setattr(dbus, "SessionBus", _require_private_bus_helper(dbus.SessionBus))
        monkeypatch.setattr(dbus, "SystemBus", _require_private_bus_helper(dbus.SystemBus))
        return

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", _unreachable_bus)
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", _unreachable_bus)
    monkeypatch.setattr(bus_module, "_thread_state", threading.local())
    monkeypatch.setattr(dbus, "SessionBus", _forbid_live_bus("session"))
    monkeypatch.setattr(dbus, "SystemBus", _forbid_live_bus("system"))


@pytest.fixture(autouse=True)
def pin_init_system(monkeypatch):
    """Describe systemd hosts unless a test opts into another init system.

    Command assertions must not depend on whether the developer or CI host
    booted with systemd or OpenRC.
    """
    from blueferry import service_manager

    monkeypatch.setattr(service_manager, "init_system", lambda: service_manager.SYSTEMD)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point every BlueFerry configuration and state path at ``tmp_path``."""
    from blueferry import config

    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "LOCAL_ENV_PATH", config_dir / "local.env")
    monkeypatch.setattr(config, "SETTINGS_JSON", config_dir / "settings.json")
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "EVENTS_DB", state_dir / "events.sqlite")
    monkeypatch.setattr(config, "CONTACTS_DB", state_dir / "contacts.sqlite")
    monkeypatch.setattr(config, "CALLS_DB", state_dir / "calls.sqlite")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    return tmp_path


@pytest.fixture
def make_daemon(isolated_state, monkeypatch):
    """Build real daemons against isolated state.

    Construction performs no D-Bus or Bluetooth I/O, so tests exercise the
    daemon's actual wiring instead of hand-assembling its private fields.
    Replace a hardware-facing collaborator on the instance when a test needs
    to observe it.
    """
    from blueferry import daemon as daemon_mod
    from blueferry.obex import worker as worker_mod

    # The worker thread would otherwise open its own bus connection.
    monkeypatch.setattr(worker_mod, "initialize_obex_worker_bus", lambda: None)
    monkeypatch.setattr(worker_mod, "close_obex_worker_bus", lambda: None)
    monkeypatch.setattr(daemon_mod, "installed_release", lambda: "0.6.0-6")
    monkeypatch.setattr(daemon_mod, "installed_build_sha", lambda: None)
    built = []

    def make():
        instance = daemon_mod.Daemon()
        # Tests may swap these on the instance; always release the originals.
        built.append((instance.obex_worker, instance.storage))
        return instance

    yield make
    for worker, storage in built:
        worker.shutdown()
        storage.close()
