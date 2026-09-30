"""Optional MPRIS2 player for the iPhone's now-playing state.

Plasma's media controller, media keys, KDE Connect-style widgets and
``playerctl`` discover players by the ``org.mpris.MediaPlayer2.*`` bus-name
prefix. This adapter publishes the iPhone as one such player while AMS reports
an active media app, and releases the name otherwise so desktops do not show a
permanently stopped entry.

Privacy: MPRIS is a public, session-wide interface. Every application in the
login session can read ``Metadata`` (title, artist, album) and receives
``PropertiesChanged`` broadcasts, exactly as with any desktop music player.
That is why it is a separate opt-in (``BLUEFERRY_MEDIA_MPRIS_ENABLED``) on top
of media control, whose own Media1 API keeps details behind BlueFerry's
authenticated, rate-limited calls and a content-free signal. Method and
property calls still pass the same caller UID check and media rate buckets.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import dbus
import dbus.bus
import dbus.exceptions
import dbus.service

from blueferry.ams.constants import PlaybackState, RemoteCommandID
from blueferry.dbus_security import CallerGuard
from blueferry.errors import BlueFerryError
from blueferry.media import MediaController

log = logging.getLogger(__name__)

MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.blueferry_iphone"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = dbus.PROPERTIES_IFACE
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
TRACK_PATH_PREFIX = "/io/weirdware/BlueFerry/MediaPlayer/Track"
IDENTITY = "iPhone (BlueFerry)"
# A reported position farther than this from the extrapolated one is a seek.
SEEK_THRESHOLD_SECONDS = 1.5

_ROOT_PROPERTIES = {
    "CanQuit": "b",
    "CanRaise": "b",
    "HasTrackList": "b",
    "Identity": "s",
    "SupportedUriSchemes": "as",
    "SupportedMimeTypes": "as",
}
_PLAYER_PROPERTIES = {
    "PlaybackStatus": "s",
    "Rate": "d",
    "Metadata": "a{sv}",
    "Volume": "d",
    "Position": "x",
    "MinimumRate": "d",
    "MaximumRate": "d",
    "CanGoNext": "b",
    "CanGoPrevious": "b",
    "CanPlay": "b",
    "CanPause": "b",
    "CanSeek": "b",
    "CanControl": "b",
}
_WRITABLE = {(PLAYER_IFACE, "Volume")}


class _UnknownProperty(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownProperty"


class _UnknownInterface(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownInterface"


class _ReadOnly(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.PropertyReadOnly"


class _NotSupported(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.NotSupported"


def _property_xml(properties: dict[str, str], writable: set[str]) -> str:
    return "".join(
        f'    <property name="{name}" type="{signature}" '
        f'access="{"readwrite" if name in writable else "read"}"/>\n'
        for name, signature in properties.items()
    )


class MprisPlayer(dbus.service.Object):
    """Map MPRIS2 to :class:`MediaController`, and own the name while active."""

    def __init__(
        self,
        connection: dbus.connection.Connection,
        media: MediaController,
        caller_guard: CallerGuard,
        *,
        clock: Callable[[], float] = time.monotonic,
        bus_name: str = MPRIS_BUS_NAME,
    ) -> None:
        super().__init__(connection, MPRIS_PATH)
        self._connection = connection
        self._media = media
        self._guard = caller_guard
        self._clock = clock
        self._bus_name = bus_name
        self._owned = False
        self._last_properties: dict[str, object] = {}
        self._track_identity: tuple | None = None
        self._track_serial = 0
        self._last_position: tuple[float, float, float] | None = None
        self._closed = False
        media.add_listener(self.refresh)
        self.refresh()

    # ---- ownership and change propagation -------------------------------

    @property
    def owned(self) -> bool:
        return self._owned

    def _should_own(self) -> bool:
        state = self._media.state
        return self._media.available and (
            state.playback_state is not None or state.title is not None
        )

    def refresh(self) -> None:
        """Called after each coalesced now-playing change."""
        if self._closed:
            return
        self._update_track_identity()
        if not self._should_own():
            self._release()
            return
        if not self._owned:
            self._acquire()
            if not self._owned:
                return
            self._last_properties = self._player_properties()
            self._remember_position()
            return
        current = self._player_properties()
        changed = {
            name: value
            for name, value in current.items()
            if self._last_properties.get(name) != value
        }
        self._last_properties = current
        if changed:
            self.PropertiesChanged(
                PLAYER_IFACE,
                dbus.Dictionary(changed, signature="sv"),
                dbus.Array([], signature="s"),
            )
        self._maybe_emit_seeked()

    def _acquire(self) -> None:
        try:
            result = self._connection.request_name(
                self._bus_name, dbus.bus.NAME_FLAG_DO_NOT_QUEUE,
            )
        except dbus.exceptions.DBusException as error:
            log.warning("could not publish MPRIS player: %s", error.get_dbus_name())
            return
        if result in (
            dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER,
            dbus.bus.REQUEST_NAME_REPLY_ALREADY_OWNER,
        ):
            self._owned = True
            log.info("published iPhone MPRIS player")
        else:
            log.warning("MPRIS player name is owned by another process")

    def _release(self) -> None:
        if not self._owned:
            return
        self._owned = False
        self._last_properties = {}
        self._last_position = None
        try:
            self._connection.release_name(self._bus_name)
        except dbus.exceptions.DBusException:
            log.debug("could not release MPRIS player name", exc_info=True)
        log.info("withdrew iPhone MPRIS player")

    def close(self) -> None:
        self._closed = True
        self._media.remove_listener(self.refresh)
        self._release()
        try:
            self.remove_from_connection()
        except Exception:
            log.debug("could not unexport MPRIS player", exc_info=True)

    def _update_track_identity(self) -> None:
        state = self._media.state
        identity = (state.title, state.artist, state.album, state.duration)
        if identity != self._track_identity:
            self._track_identity = identity
            self._track_serial += 1
            self._last_position = None

    def _remember_position(self) -> None:
        state = self._media.state
        if state.elapsed is None or state.elapsed_at is None:
            self._last_position = None
            return
        rate = (state.playback_rate or 0.0) if state.playing else 0.0
        self._last_position = (state.elapsed, state.elapsed_at, rate)

    def _maybe_emit_seeked(self) -> None:
        previous = self._last_position
        self._remember_position()
        current = self._last_position
        if previous is None or current is None or current[:2] == previous[:2]:
            return
        elapsed, at, rate = previous
        expected = elapsed + rate * max(0.0, current[1] - at)
        if abs(current[0] - expected) > SEEK_THRESHOLD_SECONDS:
            self.Seeked(dbus.Int64(self._position_us()))

    # ---- property values ------------------------------------------------

    def _position_us(self) -> int:
        position = self._media.state.position(self._clock())
        return int((position or 0.0) * 1_000_000)

    def _metadata(self) -> dbus.Dictionary:
        state = self._media.state
        metadata: dict[str, object] = {
            "mpris:trackid": dbus.ObjectPath(
                f"{TRACK_PATH_PREFIX}/{self._track_serial}"
                if state.title or state.artist or state.album else NO_TRACK
            ),
        }
        if state.title:
            metadata["xesam:title"] = dbus.String(state.title)
        if state.artist:
            metadata["xesam:artist"] = dbus.Array([state.artist], signature="s")
        if state.album:
            metadata["xesam:album"] = dbus.String(state.album)
        if state.duration:
            metadata["mpris:length"] = dbus.Int64(int(state.duration * 1_000_000))
        return dbus.Dictionary(metadata, signature="sv")

    def _supports(self, *commands: RemoteCommandID) -> bool:
        supported = self._media.state.supported_commands
        return self._media.available and any(command in supported for command in commands)

    def _player_properties(self) -> dict[str, object]:
        state = self._media.state
        if not self._media.available or state.playback_state is None:
            status = "Stopped"
        elif state.playback_state == PlaybackState.Paused:
            status = "Paused"
        else:
            status = "Playing"
        toggle = RemoteCommandID.TogglePlayPause
        return {
            "PlaybackStatus": dbus.String(status),
            "Rate": dbus.Double(1.0),
            "Metadata": self._metadata(),
            "Volume": dbus.Double(state.volume if state.volume is not None else 0.0),
            "MinimumRate": dbus.Double(1.0),
            "MaximumRate": dbus.Double(1.0),
            "CanGoNext": dbus.Boolean(self._supports(RemoteCommandID.NextTrack)),
            "CanGoPrevious": dbus.Boolean(self._supports(RemoteCommandID.PreviousTrack)),
            "CanPlay": dbus.Boolean(self._supports(RemoteCommandID.Play, toggle)),
            "CanPause": dbus.Boolean(self._supports(RemoteCommandID.Pause, toggle)),
            "CanSeek": dbus.Boolean(False),
            "CanControl": dbus.Boolean(True),
        }

    def _root_properties(self) -> dict[str, object]:
        return {
            "CanQuit": dbus.Boolean(False),
            "CanRaise": dbus.Boolean(False),
            "HasTrackList": dbus.Boolean(False),
            "Identity": dbus.String(IDENTITY),
            "SupportedUriSchemes": dbus.Array([], signature="s"),
            "SupportedMimeTypes": dbus.Array([], signature="s"),
        }

    def _all(self, interface: str) -> dict[str, object]:
        if interface == ROOT_IFACE:
            return self._root_properties()
        if interface == PLAYER_IFACE:
            values = self._player_properties()
            values["Position"] = dbus.Int64(self._position_us())
            return values
        raise _UnknownInterface(f"no such interface: {interface}")

    def _authorize(self, sender, action: str) -> None:
        try:
            self._guard.authorize(sender, action)
        except BlueFerryError as error:
            raise dbus.exceptions.DBusException(
                str(error), name=f"io.weirdware.BlueFerry.Error.{error.dbus_suffix}",
            ) from None

    # ---- org.freedesktop.DBus.Properties --------------------------------

    @dbus.service.method(
        PROPERTIES_IFACE, in_signature="ss", out_signature="v", sender_keyword="sender",
    )
    def Get(self, interface: str, name: str, sender=None):
        self._authorize(sender, "media-read")
        values = self._all(str(interface))
        if name not in values:
            raise _UnknownProperty(f"no such property: {name}")
        return values[name]

    @dbus.service.method(
        PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}", sender_keyword="sender",
    )
    def GetAll(self, interface: str, sender=None):
        self._authorize(sender, "media-read")
        return dbus.Dictionary(self._all(str(interface)), signature="sv")

    @dbus.service.method(
        PROPERTIES_IFACE, in_signature="ssv", out_signature="", sender_keyword="sender",
    )
    def Set(self, interface: str, name: str, value, sender=None) -> None:
        if (str(interface), str(name)) not in _WRITABLE:
            if str(name) in self._all(str(interface)):
                raise _ReadOnly(f"property is read-only: {name}")
            raise _UnknownProperty(f"no such property: {name}")
        self._authorize(sender, "media-command")
        # AMS has no absolute volume; move one iPhone volume step toward the
        # requested level. Plasma sends a new value per scroll step.
        current = self._media.state.volume
        try:
            target = float(value)
        except (TypeError, ValueError):
            raise dbus.exceptions.DBusException(
                "volume must be a number", name="org.freedesktop.DBus.Error.InvalidArgs",
            ) from None
        if current is None or abs(target - current) < 0.01:
            return
        self._command("volume-up" if target > current else "volume-down")

    @dbus.service.signal(PROPERTIES_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        """Standard property change notification (MPRIS clients rely on it)."""

    # ---- org.mpris.MediaPlayer2 -----------------------------------------

    @dbus.service.method(ROOT_IFACE, in_signature="", out_signature="")
    def Raise(self) -> None:
        """CanRaise is false; the iPhone has no desktop window to raise."""

    @dbus.service.method(ROOT_IFACE, in_signature="", out_signature="")
    def Quit(self) -> None:
        """CanQuit is false; the backend lifetime is not the player's."""

    # ---- org.mpris.MediaPlayer2.Player ----------------------------------

    def _command(self, name: str) -> None:
        """Best effort: MPRIS says unsupported actions have no effect."""
        try:
            self._media.send_command(
                name,
                lambda: None,
                lambda error: log.info("MPRIS media command failed: %s", type(error).__name__),
            )
        except BlueFerryError as error:
            log.debug("MPRIS media command ignored: %s", error)

    def _player_call(self, sender, name: str) -> None:
        self._authorize(sender, "media-command")
        self._command(name)

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def Next(self, sender=None) -> None:
        self._player_call(sender, "next")

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def Previous(self, sender=None) -> None:
        self._player_call(sender, "previous")

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def Pause(self, sender=None) -> None:
        self._player_call(sender, "pause")

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def PlayPause(self, sender=None) -> None:
        self._player_call(sender, "toggle")

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def Stop(self, sender=None) -> None:
        # AMS has no stop; pausing is the closest non-destructive action.
        self._player_call(sender, "pause")

    @dbus.service.method(PLAYER_IFACE, in_signature="", out_signature="", sender_keyword="sender")
    def Play(self, sender=None) -> None:
        self._player_call(sender, "play")

    @dbus.service.method(PLAYER_IFACE, in_signature="x", out_signature="", sender_keyword="sender")
    def Seek(self, offset: int, sender=None) -> None:
        # CanSeek is false: AMS only offers fixed skip steps. Map relative
        # seeks to those steps for clients that call Seek anyway.
        self._authorize(sender, "media-command")
        if int(offset) > 0:
            self._command("skip-forward")
        elif int(offset) < 0:
            self._command("skip-backward")

    @dbus.service.method(PLAYER_IFACE, in_signature="ox", out_signature="", sender_keyword="sender")
    def SetPosition(self, track_id, position: int, sender=None) -> None:
        """CanSeek is false; absolute positioning is not available over AMS."""
        self._authorize(sender, "media-command")

    @dbus.service.method(PLAYER_IFACE, in_signature="s", out_signature="", sender_keyword="sender")
    def OpenUri(self, uri: str, sender=None) -> None:
        self._authorize(sender, "media-command")
        raise _NotSupported("the iPhone player cannot open URIs")

    @dbus.service.signal(PLAYER_IFACE, signature="x")
    def Seeked(self, position):
        """The iPhone reported a position discontinuity (in microseconds)."""

    # ---- introspection with properties ----------------------------------

    @dbus.service.method(
        dbus.INTROSPECTABLE_IFACE, in_signature="", out_signature="s",
        path_keyword="object_path", connection_keyword="connection",
    )
    def Introspect(self, object_path, connection):
        xml = dbus.service.Object.Introspect(self, object_path, connection)
        for interface, properties, writable in (
            (ROOT_IFACE, _ROOT_PROPERTIES, set()),
            (PLAYER_IFACE, _PLAYER_PROPERTIES, {"Volume"}),
        ):
            marker = f'  <interface name="{interface}">\n'
            xml = xml.replace(marker, marker + _property_xml(properties, writable), 1)
        return xml
