"""Client side of the plugin contract: talk to one plugin, distrust it.

Calls are synchronous dbus-python calls with explicit timeouts; clients run
them on a worker thread (Qt task, Textual worker) or in the CLI, never on a
UI thread. Every reply is size-limited, parsed as JSON and validated; paths
a plugin hands out must be owner-only regular files inside the plugin's cache
directory. Nothing a plugin returns is rendered as markup.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import (
    MAX_RECENT_PHOTOS,
    MAX_REPLY_BYTES,
    OBJECT_PATH,
    PHOTOS_INTERFACE,
    PLUGIN_INTERFACE,
)
from .manifest import PluginManifest

STATUS_TIMEOUT_SEC = 10.0
LIST_TIMEOUT_SEC = 90.0
FETCH_TIMEOUT_SEC = 600.0
_ASSET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TIMESTAMP = re.compile(r"^[0-9T:.+\-Z ]{4,40}$")
_PHOTO_TYPES = frozenset({"image", "video", "other"})
_STATES = frozenset({"ok", "unconfigured", "error", "busy"})
_TOKEN = re.compile(r"^[a-z0-9-]{1,40}$")


class PluginError(Exception):
    """A plugin is missing, misbehaving or reported an error."""


class Transport(Protocol):
    def call(
        self, bus_name: str, interface: str, method: str, signature: str,
        args: tuple, timeout: float,
    ) -> object: ...

    def owner_uid(self, bus_name: str) -> int | None: ...


class DBusTransport:
    """Session-bus transport; a plugin is reachable only on the user's bus."""

    def __init__(self, bus: Any = None) -> None:
        self._bus = bus

    def _connection(self) -> Any:
        if self._bus is None:
            import dbus

            self._bus = dbus.SessionBus()
        return self._bus

    def call(
        self, bus_name: str, interface: str, method: str, signature: str,
        args: tuple, timeout: float,
    ) -> object:
        import dbus.exceptions

        bus = self._connection()
        try:
            # Activation happens here: the bus starts the plugin from its
            # D-Bus service file when the name has no owner yet.
            proxy = bus.get_object(bus_name, OBJECT_PATH, introspect=False)
            return getattr(proxy, method)(
                *args, dbus_interface=interface, signature=signature, timeout=timeout,
            )
        except dbus.exceptions.DBusException as error:
            raise PluginError(_error_text(error)) from None

    def owner_uid(self, bus_name: str) -> int | None:
        import dbus.exceptions

        try:
            return int(self._connection().get_unix_user(bus_name))
        except dbus.exceptions.DBusException:
            return None


def _error_text(error: Any) -> str:
    name = str(error.get_dbus_name() or "")
    if name in (
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    ):
        return "the plugin is not installed or could not be started"
    if name == "org.freedesktop.DBus.Error.NoReply":
        return "the plugin did not answer in time"
    message = str(error.get_dbus_message() or name)
    return plain_text(message, 200) or "the plugin failed"


def plain_text(value: object, limit: int) -> str:
    """One line of printable text, at most ``limit`` characters."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return " ".join(text.split())[:limit]


@dataclass(frozen=True, slots=True)
class PluginStatus:
    state: str
    detail: str = ""
    server: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ok"


@dataclass(frozen=True, slots=True)
class Photo:
    id: str
    taken_at: str
    type: str
    thumbnail: Path | None
    original: Path | None = None


class PluginClient:
    """One plugin's Plugin1 (and capability) methods, validated."""

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        transport: Transport | None = None,
        uid: int | None = None,
        cache_root: Callable[[], Path] | None = None,
    ) -> None:
        self.manifest = manifest
        self._transport = transport or DBusTransport()
        self._uid = os.getuid() if uid is None else uid
        self._cache_root = cache_root or default_cache_root

    def _call(self, interface: str, method: str, signature: str, args: tuple,
              timeout: float) -> str:
        reply = self._transport.call(
            self.manifest.bus_name, interface, method, signature, args, timeout,
        )
        # The name may only now have an owner (activation). Refuse a plugin
        # run by somebody else, even on a shared bus.
        owner = self._transport.owner_uid(self.manifest.bus_name)
        if owner != self._uid:
            raise PluginError("the plugin runs as a different user; ignoring it")
        if not isinstance(reply, str):
            raise PluginError("the plugin sent an invalid reply")
        if len(reply.encode("utf-8", "surrogatepass")) > MAX_REPLY_BYTES:
            raise PluginError("the plugin sent an oversized reply")
        return str(reply)

    def _json(self, interface: str, method: str, signature: str, args: tuple,
              timeout: float) -> object:
        try:
            return json.loads(self._call(interface, method, signature, args, timeout))
        except ValueError:
            raise PluginError("the plugin sent invalid JSON") from None

    def status(self) -> PluginStatus:
        value = self._json(PLUGIN_INTERFACE, "Status", "", (), STATUS_TIMEOUT_SEC)
        if not isinstance(value, Mapping):
            raise PluginError("the plugin sent an invalid status")
        state = value.get("state")
        if state not in _STATES:
            raise PluginError("the plugin sent an invalid status")
        return PluginStatus(
            state=str(state),
            detail=plain_text(value.get("detail", ""), 200),
            server=plain_text(value.get("server", ""), 100),
        )

    # ---- photos capability ---------------------------------------------------

    def list_recent(self, limit: int) -> list[Photo]:
        bounded = max(1, min(int(limit), MAX_RECENT_PHOTOS))
        value = self._json(
            PHOTOS_INTERFACE, "ListRecent", "u", (bounded,), LIST_TIMEOUT_SEC,
        )
        if not isinstance(value, list):
            raise PluginError("the plugin sent an invalid photo list")
        photos = []
        seen: set[str] = set()
        for entry in value[:bounded]:
            photo = self._photo(entry)
            if photo is not None and photo.id not in seen:
                seen.add(photo.id)
                photos.append(photo)
        return photos

    def fetch_original(self, photo_id: str) -> Path:
        if not _ASSET_ID.fullmatch(photo_id):
            raise PluginError("not a photo id")
        path = self.checked_path(
            self._call(PHOTOS_INTERFACE, "FetchOriginal", "s", (photo_id,), FETCH_TIMEOUT_SEC)
        )
        if path is None:
            raise PluginError("the plugin returned an unusable file")
        return path

    def _photo(self, entry: object) -> Photo | None:
        if not isinstance(entry, Mapping):
            return None
        photo_id, taken_at, kind = entry.get("id"), entry.get("taken_at"), entry.get("type")
        if not isinstance(photo_id, str) or not _ASSET_ID.fullmatch(photo_id):
            return None
        if not isinstance(taken_at, str) or not _TIMESTAMP.fullmatch(taken_at):
            taken_at = ""
        if kind not in _PHOTO_TYPES:
            kind = "other"
        return Photo(
            id=photo_id,
            taken_at=str(taken_at),
            type=str(kind),
            thumbnail=self.checked_path(entry.get("thumbnail")),
            original=self.checked_path(entry.get("original")),
        )

    def checked_path(self, value: object) -> Path | None:
        """A plugin-supplied path, only if it is our own file in our cache."""
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            return None
        path = Path(value)
        if not path.is_absolute():
            return None
        try:
            resolved = path.resolve(strict=True)
            root = self._cache_root().resolve()
            info = os.lstat(resolved)
        except (OSError, RuntimeError):
            return None
        if root not in resolved.parents:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self._uid:
            return None
        return resolved


def default_cache_root() -> Path:
    """``$XDG_CACHE_HOME/blueferry``: the only place plugin files may live."""
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return Path(cache_home) / "blueferry"
