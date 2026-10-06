"""Route the iPhone's media playback to this computer or back to the phone.

The phone is an A2DP *Source* (UUID 0x110A). Asking bluetoothd to
``Device1.ConnectProfile`` that UUID runs BlueZ's ``source_connect()``
(``profiles/audio/source.c`` in 5.87): the computer, acting as A2DP sink,
discovers the phone's stream endpoints, configures one, and opens the AVDTP
stream. bluetoothd then exports an ``org.bluez.MediaTransport1`` whose
``UUID`` is the local sink role (0x110B). The phone, not the computer, starts
the stream (AVDTP START) when something plays; iOS normally offers the open
link as an audio route and picks it. ``DisconnectProfile`` closes the stream
(``source_disconnect()`` → ``avdtp_close``) and iOS falls back to its speaker.
The open stream is what this module reports as ``pc``; whether audio actually
plays is up to the phone. None of the iOS behaviour is verified on hardware.

Only meaningful when BlueFerry does not forbid the sink role, i.e. with
``BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false``; otherwise the WirePlumber policy
strips ``a2dp_sink`` and the route is reported as unavailable.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from blueferry.errors import InvalidArgumentsError, NotReadyError
from blueferry.tether import AsyncBus, dbus_error_name

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
TRANSPORT_IFACE = "org.bluez.MediaTransport1"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"

ROUTE_PC = "pc"
ROUTE_PHONE = "phone"
ROUTE_UNAVAILABLE = "unavailable"
ROUTES = frozenset({ROUTE_PC, ROUTE_PHONE})

# Content-free reason tokens; clients translate them.
REASON_POLICY = "keep_phone_audio_on_phone"
REASON_DISCONNECTED = "phone_disconnected"
REASON_NO_SOURCE = "no_a2dp_source"
REASON_UNKNOWN = "unknown"

# ConnectProfile pages the phone and negotiates AVDTP; bluetoothd bounds the
# individual steps itself, so this is only a backstop.
PROFILE_CALL_TIMEOUT_SEC = 30.0
# Re-read the object tree once after a change in case bluetoothd's signals
# raced the reply.
SETTLE_SEC = 2
# iOS pauses playback when its A2DP output goes away, as it does when
# headphones are unplugged. Resume once the phone has settled on its speaker.
RESUME_AFTER_HANDBACK_SEC = 2

_ALREADY = frozenset({
    "org.bluez.Error.AlreadyConnected",
    "org.bluez.Error.NotConnected",
})

Schedule = Callable[[int, Callable[[], bool]], int]
Cancel = Callable[[int], object]


class PhoneAudioRoute:
    """Content-free view of, and control over, the phone's A2DP stream."""

    def __init__(
        self,
        bus: Callable[[], AsyncBus],
        device_path: str,
        *,
        allowed: bool,
        on_changed: Callable[[], None],
        schedule: Schedule,
        cancel: Cancel,
        was_playing: Callable[[], bool] | None = None,
        resume_playback: Callable[[], None] | None = None,
    ) -> None:
        self._bus = bus
        self._device_path = device_path
        self._allowed = allowed
        self._on_changed = on_changed
        self._schedule = schedule
        self._cancel = cancel
        self._matches: list[Any] = []
        self._connected = False
        self._has_source = False
        self._transports: set[str] = set()
        self._known = False
        self._pending: str | None = None
        self._generation = 0
        self._settle_id: int | None = None
        self._resume_id: int | None = None
        self._was_playing = was_playing
        self._resume_playback = resume_playback
        self._stopped = False

    # ---- state ---------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        route, reason = self._route()
        return {
            "phone_audio_route": route,
            "phone_audio_reason": reason,
            "phone_audio_pending": self._pending or "",
        }

    def _route(self) -> tuple[str, str]:
        if not self._allowed:
            return ROUTE_UNAVAILABLE, REASON_POLICY
        if not self._known:
            return ROUTE_UNAVAILABLE, REASON_UNKNOWN
        if not self._connected:
            return ROUTE_UNAVAILABLE, REASON_DISCONNECTED
        if not self._has_source:
            return ROUTE_UNAVAILABLE, REASON_NO_SOURCE
        return (ROUTE_PC if self._transports else ROUTE_PHONE), ""

    def _changed(self, before: dict[str, object]) -> None:
        if self.snapshot() != before:
            self._on_changed()

    # ---- observation ---------------------------------------------------

    def start(self) -> None:
        if not self._allowed or self._matches:
            return
        self._stopped = False
        bus = self._bus()
        self._matches = [
            bus.add_signal_receiver(
                self._interfaces_added, signal_name="InterfacesAdded",
                dbus_interface=OBJECT_MANAGER_IFACE, bus_name=BLUEZ,
            ),
            bus.add_signal_receiver(
                self._interfaces_removed, signal_name="InterfacesRemoved",
                dbus_interface=OBJECT_MANAGER_IFACE, bus_name=BLUEZ,
            ),
            bus.add_signal_receiver(
                self._properties_changed, signal_name="PropertiesChanged",
                dbus_interface=PROPERTIES_IFACE, bus_name=BLUEZ,
                path=self._device_path, arg0=DEVICE_IFACE,
            ),
        ]
        self.probe()

    def probe(self) -> None:
        """Re-read the device and its transports from bluetoothd."""
        self._generation += 1
        generation = self._generation

        def reply(objects: Mapping) -> None:
            if generation != self._generation:
                return
            before = self.snapshot()
            self._apply_tree(objects)
            self._changed(before)

        def failed(error: Exception) -> None:
            if generation != self._generation:
                return
            log.debug("could not read the phone's audio state: %s", dbus_error_name(error))
            before = self.snapshot()
            self._known = False
            self._changed(before)

        try:
            self._bus().call_async(
                BLUEZ, "/", OBJECT_MANAGER_IFACE, "GetManagedObjects", "", (),
                reply, failed, timeout=5.0,
            )
        except Exception as error:
            failed(error)

    def _apply_tree(self, objects: Mapping) -> None:
        device = objects.get(self._device_path, {}).get(DEVICE_IFACE)
        self._known = True
        self._apply_device(device or {}, replace=True)
        self._transports = {
            str(path)
            for path, interfaces in objects.items()
            if self._is_phone_sink(interfaces.get(TRANSPORT_IFACE))
        }

    def _apply_device(self, properties: Mapping, *, replace: bool = False) -> None:
        if replace or "Connected" in properties:
            self._connected = bool(properties.get("Connected", False))
        if replace or "UUIDs" in properties:
            uuids = {str(uuid).casefold() for uuid in properties.get("UUIDs", ())}
            self._has_source = A2DP_SOURCE_UUID in uuids
        if not self._connected:
            self._transports.clear()

    def _is_phone_sink(self, properties: Mapping | None) -> bool:
        if not properties:
            return False
        return (
            str(properties.get("Device", "")) == self._device_path
            and str(properties.get("UUID", "")).casefold() == A2DP_SINK_UUID
        )

    def _interfaces_added(self, path, interfaces) -> None:
        if not self._is_phone_sink(interfaces.get(TRANSPORT_IFACE)):
            return
        self._generation += 1  # a live signal supersedes an older probe
        before = self.snapshot()
        self._transports.add(str(path))
        self._changed(before)

    def _interfaces_removed(self, path, interfaces) -> None:
        if TRANSPORT_IFACE not in interfaces or str(path) not in self._transports:
            return
        self._generation += 1
        before = self.snapshot()
        self._transports.discard(str(path))
        self._changed(before)

    def _properties_changed(self, interface, changed, _invalidated) -> None:
        if str(interface) != DEVICE_IFACE:
            return
        if "Connected" not in changed and "UUIDs" not in changed:
            return
        self._generation += 1
        before = self.snapshot()
        self._known = True
        self._apply_device(changed)
        self._changed(before)

    # ---- control -------------------------------------------------------

    def set_route(
        self,
        route: str,
        success: Callable[[str], None],
        failure: Callable[[Exception], None],
    ) -> None:
        if route not in ROUTES:
            raise InvalidArgumentsError("route must be 'pc' or 'phone'")
        current, reason = self._route()
        if current == ROUTE_UNAVAILABLE:
            if reason == REASON_POLICY:
                raise NotReadyError(
                    "phone audio stays on the iPhone; set "
                    "BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false to allow it here"
                )
            raise NotReadyError("the iPhone's audio route is unavailable")
        if self._pending is not None:
            raise NotReadyError("an audio route change is already in progress")
        before = self.snapshot()
        resume = (
            route == ROUTE_PHONE
            and self._was_playing is not None
            and self._resume_playback is not None
            and self._was_playing()
        )
        self._pending = route
        self._changed(before)
        method = "ConnectProfile" if route == ROUTE_PC else "DisconnectProfile"

        def done() -> None:
            if self._stopped:
                # A late BlueZ reply after stop() touches no timer or bus.
                return
            before = self.snapshot()
            self._pending = None
            self._changed(before)
            self._settle()

        def reply(*_args) -> None:
            done()
            if resume and not self._stopped:
                self._schedule_resume()
            success(route)

        def failed(error: Exception) -> None:
            name = dbus_error_name(error)
            if name in _ALREADY:
                reply()
                return
            log.info("phone audio %s failed: %s", method, name)
            done()
            if name == "org.bluez.Error.InProgress":
                failure(NotReadyError(
                    "bluetoothd is still changing the iPhone's audio; try again shortly"
                ))
                return
            failure(error)

        try:
            self._bus().call_async(
                BLUEZ, self._device_path, DEVICE_IFACE, method, "s",
                (A2DP_SOURCE_UUID,), reply, failed, timeout=PROFILE_CALL_TIMEOUT_SEC,
            )
        except Exception as error:
            failed(error)

    def _schedule_resume(self) -> None:
        if self._resume_id is not None:
            self._cancel(self._resume_id)

        def fire() -> bool:
            self._resume_id = None
            if not self._stopped and self._resume_playback is not None:
                try:
                    self._resume_playback()
                except Exception as error:  # media control may have gone away
                    log.info("could not resume iPhone playback: %s", type(error).__name__)
            return False

        self._resume_id = self._schedule(RESUME_AFTER_HANDBACK_SEC, fire)

    def _settle(self) -> None:
        if self._settle_id is not None:
            self._cancel(self._settle_id)

        def fire() -> bool:
            self._settle_id = None
            self.probe()
            return False

        self._settle_id = self._schedule(SETTLE_SEC, fire)

    def stop(self) -> None:
        self._stopped = True
        self._pending = None
        if self._resume_id is not None:
            self._cancel(self._resume_id)
            self._resume_id = None
        self._generation += 1
        if self._settle_id is not None:
            self._cancel(self._settle_id)
            self._settle_id = None
        matches, self._matches = self._matches, []
        for match in matches:
            try:
                match.remove()
            except Exception as error:
                log.debug("could not remove audio route watch: %s", dbus_error_name(error))
