"""Battery Service (GATT 0x180F) client on the LE bond that carries ANCS.

The iPhone exports the standard Battery Service in its GATT database. Its
Battery Level characteristic (0x2A19) reports the charge in 1 % steps, far
finer than the 20 % HFP ``battchg`` indicator. Like the AMS client, this
client never opens a connection of its own: it follows the bearer
supervisor's LE link, reads the level once when the link has settled and
then subscribes to notifications.

Every BlueZ call is asynchronous (``reply_handler``/``error_handler``) so the
daemon's GLib loop is never blocked. Replies that arrive after a bearer
reset, a BlueZ owner change or ``stop()`` are discarded by generation.

Like the AMS client, this client never calls ``StopNotify``: bluetoothd 5.87
crashes when a CCC enable completes after its registration was freed during
an LE flap (see PROTOCOL.md). The registration is released when the
daemon's D-Bus connection closes. (ANCS keeps one guarded, synchronous
StopNotify for dead registrations while ATT is settled; nothing here needs
that.)

The level is a personal reading: it only reaches GetStatus (unicast), never
a signal or a log line.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import dbus
import dbus.exceptions
from gi.repository import GLib

from blueferry import dbus_call
from blueferry.bus import get_system_bus

log = logging.getLogger(__name__)

BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
DBUS_CALL_TIMEOUT_SECONDS = 10
BEARER_SETTLE_SECONDS = 3
RETRY_INITIAL_SECONDS = 2
RETRY_MAX_SECONDS = 120

_BLUEZ = "org.bluez"
_GATT_CHAR = "org.bluez.GattCharacteristic1"
_PROPERTIES = "org.freedesktop.DBus.Properties"
_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"


def parse_battery_level(value: object) -> int | None:
    """Battery Level is one uint8 (0-100 %); anything else is unknown."""
    try:
        raw = bytes(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    if len(raw) != 1 or raw[0] > 100:
        return None
    return raw[0]


def _char_path_to_device_path(char_path: str) -> str:
    return "/".join(char_path.rsplit("/", 2)[:-2])


def _error_name(error: Exception) -> str:
    if isinstance(error, dbus.exceptions.DBusException):
        return error.get_dbus_name() or type(error).__name__
    return type(error).__name__


class BatteryServiceClient:
    """Keeps the iPhone's Battery Level while the LE link is up."""

    def __init__(
        self,
        device_path: str,
        *,
        on_level: Callable[[int | None], None],
        bus_factory: Callable[[], Any] = get_system_bus,
        schedule: Callable[[int, Callable[[], bool]], int] = GLib.timeout_add_seconds,
        cancel: Callable[[int], object] = GLib.source_remove,
    ) -> None:
        self.device_path = device_path
        self._on_level = on_level
        self._bus_factory = bus_factory
        self._schedule = schedule
        self._cancel = cancel

        self._path: str | None = None
        self._started = False
        self._generation = 0
        self._manager_generation = 0
        self._bearer_connected: bool | None = None
        self._bearer_ready = False
        self._subscribing = False
        self._subscribed = False
        self._notify_owned = False
        self._level: int | None = None
        self._manager_matches: list = []
        self._value_match: Any = None
        self._settle_id: int | None = None
        self._retry_id: int | None = None
        self._retry_delay = RETRY_INITIAL_SECONDS

    @property
    def level(self) -> int | None:
        """Last Battery Level on the current LE link, or ``None``."""
        return self._level

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._bind_manager()
        if self._bearer_connected is True:
            self._schedule_settle()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._bearer_ready = False
        self._cancel_timer("_settle_id")
        self._reset()
        self._remove_manager_matches()
        self._path = None
        self._notify_owned = False

    def observe_bearer_state(self, connected: bool | None) -> None:
        previous = self._bearer_connected
        self._bearer_connected = connected
        if connected is not True:
            self._bearer_ready = False
            self._cancel_timer("_settle_id")
            self._reset()
            return
        if previous is not True:
            # A fresh link starts a fresh backoff; failures of the last
            # link say nothing about this one.
            self._retry_delay = RETRY_INITIAL_SECONDS
            if self._started:
                self._schedule_settle()

    def observe_bluez_owner(self, old_owner, new_owner) -> None:
        if not self._started:
            return
        if old_owner:
            self._bearer_ready = False
            self._bearer_connected = None
            self._cancel_timer("_settle_id")
            self._reset()
            self._remove_manager_matches()
            self._path = None
            self._notify_owned = False
        if new_owner:
            self._bind_manager()

    # ---- discovery ------------------------------------------------------

    def _bind_manager(self) -> None:
        self._remove_manager_matches()
        bus = self._bus_factory()
        self._manager_generation += 1
        generation = self._manager_generation
        self._manager_matches = [
            bus.add_signal_receiver(
                self._on_iface_added, dbus_interface=_OBJECT_MANAGER,
                signal_name="InterfacesAdded", bus_name=_BLUEZ, path="/",
            ),
            bus.add_signal_receiver(
                self._on_iface_removed, dbus_interface=_OBJECT_MANAGER,
                signal_name="InterfacesRemoved", bus_name=_BLUEZ, path="/",
            ),
        ]

        def swept(managed) -> None:
            if not self._started or generation != self._manager_generation:
                return
            for path, interfaces in managed.items():
                self._on_iface_added(path, interfaces)

        def failed(error) -> None:
            log.warning("battery service object sweep failed: %s", _error_name(error))

        dbus_call.call_async(
            bus.get_object(_BLUEZ, "/", introspect=False),
            _OBJECT_MANAGER, "GetManagedObjects", "",
            reply_handler=swept,
            error_handler=failed,
            timeout=DBUS_CALL_TIMEOUT_SECONDS,
        )

    def _on_iface_added(self, path, interfaces) -> None:
        if not self._started:
            return
        characteristic = interfaces.get(_GATT_CHAR)
        if characteristic is None:
            return
        path_s = str(path)
        if (
            str(characteristic.get("UUID", "")).lower() != BATTERY_LEVEL_CHAR
            or _char_path_to_device_path(path_s) != self.device_path
            or self._path == path_s
        ):
            return
        self._path = path_s
        self._notify_owned = False
        log.info("iPhone Battery Level characteristic found")
        self._try_subscribe()

    def _on_iface_removed(self, path, _interfaces) -> None:
        if self._path is not None and str(path) == self._path:
            log.info("iPhone Battery Level characteristic removed")
            self._path = None
            self._notify_owned = False
            self._reset()

    def _remove_manager_matches(self) -> None:
        self._manager_generation += 1
        for match in self._manager_matches:
            try:
                match.remove()
            except Exception:
                log.debug("could not remove battery service watch", exc_info=True)
        self._manager_matches = []

    # ---- subscription ---------------------------------------------------

    def _schedule_settle(self) -> None:
        if self._settle_id is None:
            self._settle_id = self._schedule(BEARER_SETTLE_SECONDS, self._settled)

    def _settled(self) -> bool:
        self._settle_id = None
        if self._started and self._bearer_connected is True:
            self._bearer_ready = True
            self._try_subscribe()
        return False

    def _try_subscribe(self) -> None:
        if (
            not self._started
            or self._subscribing
            or self._subscribed
            or self._retry_id is not None
            or self._bearer_connected is not True
            or not self._bearer_ready
            or self._path is None
        ):
            return
        self._subscribing = True
        generation = self._generation
        path = self._path
        characteristic = self._bus_factory().get_object(_BLUEZ, path, introspect=False)
        # Install the receiver first so no notification is lost between the
        # CCC write and signal registration.
        self._value_match = self._bus_factory().add_signal_receiver(
            self._on_value_changed, dbus_interface=_PROPERTIES,
            signal_name="PropertiesChanged", bus_name=_BLUEZ, path=path,
        )

        def current() -> bool:
            return generation == self._generation

        def failed(error: Exception) -> None:
            if not current():
                return
            log.info("iPhone battery subscription failed: %s", _error_name(error))
            self._reset()
            self._schedule_retry()

        def read_done(value) -> None:
            if not current():
                return
            self._publish(parse_battery_level(value))
            if self._notify_owned:
                # Leave a surviving CCC registration alone (module docstring).
                dbus_call.call_async(
                    characteristic, _PROPERTIES, "Get", "ss", (_GATT_CHAR, "Notifying"),
                    reply_handler=lambda on: subscribed() if bool(on) else start_notify(),
                    error_handler=lambda _error: start_notify(),
                    timeout=DBUS_CALL_TIMEOUT_SECONDS,
                )
            else:
                start_notify()

        def start_notify() -> None:
            if not current():
                return
            dbus_call.call_async(
                characteristic, _GATT_CHAR, "StartNotify", "",
                reply_handler=notify_started,
                error_handler=failed,
                timeout=DBUS_CALL_TIMEOUT_SECONDS,
            )

        def notify_started() -> None:
            if not current():
                return
            self._notify_owned = True
            subscribed()

        def subscribed() -> None:
            if not current():
                return
            self._subscribing = False
            self._subscribed = True
            self._retry_delay = RETRY_INITIAL_SECONDS
            log.info("iPhone battery level subscribed")

        try:
            dbus_call.call_async(
                characteristic, _GATT_CHAR, "ReadValue", "a{sv}", (dbus_call.options(),),
                reply_handler=read_done,
                error_handler=failed,
                timeout=DBUS_CALL_TIMEOUT_SECONDS,
            )
        except Exception as error:
            # Dispatch can fail synchronously (closed bus, marshalling); the
            # retry must still be scheduled or the client stays "subscribing".
            failed(error)

    def _schedule_retry(self) -> None:
        if not self._started or self._retry_id is not None:
            return
        delay = self._retry_delay
        self._retry_delay = min(self._retry_delay * 2, RETRY_MAX_SECONDS)
        self._retry_id = self._schedule(delay, self._retry)

    def _retry(self) -> bool:
        self._retry_id = None
        self._try_subscribe()
        return False

    def _cancel_timer(self, attribute: str) -> None:
        source = getattr(self, attribute)
        if source is None:
            return
        try:
            self._cancel(source)
        except Exception:
            log.debug("could not remove battery service timer", exc_info=True)
        setattr(self, attribute, None)

    def _reset(self) -> None:
        """Forget the subscription; late replies are discarded."""
        self._generation += 1
        self._subscribing = False
        self._subscribed = False
        self._cancel_timer("_retry_id")
        if self._value_match is not None:
            try:
                self._value_match.remove()
            except Exception:
                log.debug("could not remove battery value watch", exc_info=True)
            self._value_match = None
        self._publish(None)

    # ---- values ---------------------------------------------------------

    def _on_value_changed(self, interface, changed, _invalidated) -> None:
        if interface != _GATT_CHAR or not self._started or self._value_match is None:
            return
        value = changed.get("Value")
        if value is not None:
            self._publish(parse_battery_level(value))

    def _publish(self, level: int | None) -> None:
        if level == self._level:
            return
        self._level = level
        try:
            self._on_level(level)
        except Exception:
            log.exception("battery level callback failed")
