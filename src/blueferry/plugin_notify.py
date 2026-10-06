"""Desktop popups requested by plugins (capability ``notify``, PLUGINS.md 1.2).

A plugin emits ``Notify(title, body, icon, action_label, action_id)`` on its
``Plugin1`` interface on the session bus. The daemon shows it through its own notification sink,
so the user's policy applies (``none`` silences plugins too, and without
``BLUEFERRY_SHOW_NOTIFICATION_CONTENT`` only the plugin's name is shown).
A click on the popup's button calls the plugin's
``Plugin1.InvokeAction("notify", action_id, "{}")``.

The daemon still never loads plugin code: it reads manifests (files only)
to learn which bus names may ask for popups, checks that the signal's sender
owns such a name and runs as the same user, and limits each plugin to a few
popups a minute. Every bus call here is asynchronous.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from blueferry import __version__
from blueferry.plugin_api import (
    CAPABILITY_NOTIFY,
    MAX_REPLY_BYTES,
    METHOD_INVOKE_ACTION,
    OBJECT_PATH,
    SIGNAL_NOTIFY,
    SURFACES_INTERFACE,
)
from blueferry.plugin_api.client import plugin_cache_roots
from blueferry.plugin_api.manifest import PluginManifest, discover
from blueferry.plugin_api.surfaces import (
    NOTIFY_ITEM_ID,
    Notification,
    SurfaceError,
    checked_open_uri,
    parse_action_result,
    parse_notification,
)
from blueferry.plugin_prefs import disabled_plugins

log = logging.getLogger(__name__)

_DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus")
POPUPS_PER_MINUTE = 6
# Notify signals one sender may cause lookups for, before it is verified;
# stops any session-bus client from making the daemon scan and ask the bus
# in a loop.
LOOKUPS_PER_MINUTE = 12
# Manifests are re-read at most this often (files only, but on the loop).
MANIFEST_TTL_SEC = 30.0
MAX_VERIFIED_SENDERS = 64
ACTION_TIMEOUT_SEC = 60.0
LOOKUP_TIMEOUT_SEC = 2.0


@dataclass(frozen=True, slots=True)
class PluginPopup:
    plugin_id: str
    plugin_name: str
    bus_name: str
    note: Notification
    plugin_alias: str = ""


def notify_plugins() -> list[PluginManifest]:
    """Enabled manifests with ``notify``; files only, nothing is started."""
    disabled = disabled_plugins()
    found = discover(blueferry_version=__version__)
    return [p for p in found.with_capability(CAPABILITY_NOTIFY) if p.id not in disabled]


def gio_open_uri(uri: str) -> None:
    from gi.repository import Gio

    Gio.AppInfo.launch_default_for_uri_async(uri, None, None, None, None)


class PluginPopups:
    """Watch Plugin1.Notify and hand verified popups to ``show``."""

    def __init__(
        self,
        bus,
        *,
        show: Callable[[PluginPopup], None],
        plugins: Callable[[], Sequence[PluginManifest]] = notify_plugins,
        open_uri: Callable[[str], None] = gio_open_uri,
        uid: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._show = show
        self._plugins = plugins
        self._open_uri = open_uri
        self._uid = os.getuid() if uid is None else uid
        self._clock = clock
        self._recent: dict[str, deque[float]] = {}
        self._lookups: dict[str, deque[float]] = {}
        self._manifests: list[PluginManifest] = []
        self._manifests_at = float("-inf")
        # Unique bus names are never reused within a bus session, so a
        # sender verified once stays that plugin; this also keeps a popup
        # from a plugin that emits and then idles out right away.
        self._verified: dict[str, PluginManifest] = {}
        self._match = None

    def start(self) -> None:
        if self._match is not None:
            return
        try:
            self._match = self._bus.add_signal_receiver(
                self._on_notify, dbus_interface=SURFACES_INTERFACE, signal_name=SIGNAL_NOTIFY,
                path=OBJECT_PATH, sender_keyword="sender",
            )
        except Exception:
            log.warning("could not watch plugin popups; they stay off")

    def stop(self) -> None:
        if self._match is None:
            return
        try:
            self._match.remove()
        except Exception:
            log.debug("could not remove the plugin popup watch", exc_info=True)
        self._match = None

    # ---- incoming ---------------------------------------------------------------

    def _on_notify(self, title="", body="", icon="", action_label="", action_id="",
                   sender=None) -> None:
        note = parse_notification(title, body, icon, action_label, action_id)
        if note is None or not sender:
            return
        sender = str(sender)
        known = self._verified.get(sender)
        if known is not None:
            if any(plugin.id == known.id for plugin in self._candidates()):
                self._deliver(known, note)  # still enabled
            return
        if not self._admit(self._lookups, sender, LOOKUPS_PER_MINUTE):
            log.info("ignoring repeated popups from an unverified sender")
            return
        self._match_owner(sender, note, self._candidates())

    def _candidates(self) -> list[PluginManifest]:
        now = self._clock()
        if now - self._manifests_at >= MANIFEST_TTL_SEC:
            try:
                self._manifests = list(self._plugins())
            except Exception:
                log.debug("could not read plugin manifests", exc_info=True)
                self._manifests = []
            self._manifests_at = now
        return list(self._manifests)

    def _admit(self, table: dict[str, deque[float]], key: str, limit: int) -> bool:
        now = self._clock()
        window = table.setdefault(key, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        if len(table) > 256:
            # One-shot senders never come back to empty their window: drop
            # every entry whose newest call is older than the window.
            for stale in [name for name, times in table.items()
                          if not times or now - times[-1] > 60]:
                del table[stale]
        return True

    def _match_owner(
        self, sender: str, note: Notification, candidates: list[PluginManifest],
    ) -> None:
        """Ask the bus, one name at a time, which plugin owns ``sender``."""
        if not candidates:
            log.info("ignoring a popup from a sender that is no enabled notify plugin")
            return
        plugin, rest = candidates[0], candidates[1:]

        def owner(name) -> None:
            if str(name) == sender:
                self._check_user(sender, plugin, note)
            else:
                self._match_owner(sender, note, rest)

        self._bus.call_async(
            *_DBUS, "GetNameOwner", "s", (plugin.bus_name,), owner,
            lambda _error: self._match_owner(sender, note, rest),
            timeout=LOOKUP_TIMEOUT_SEC,
        )

    def _check_user(self, sender: str, plugin: PluginManifest, note: Notification) -> None:
        def checked(uid) -> None:
            if int(uid) != self._uid:
                log.warning("ignoring a popup from a plugin run by another user")
                return
            self._verified[sender] = plugin
            while len(self._verified) > MAX_VERIFIED_SENDERS:
                self._verified.pop(next(iter(self._verified)))
            self._deliver(plugin, note)

        self._bus.call_async(
            *_DBUS, "GetConnectionUnixUser", "s", (sender,), checked,
            lambda _error: log.debug("could not check a plugin's user"),
            timeout=LOOKUP_TIMEOUT_SEC,
        )

    def _deliver(self, plugin: PluginManifest, note: Notification) -> None:
        if not self._admit(self._recent, plugin.id, POPUPS_PER_MINUTE):
            log.info("plugin popup rate limit reached; dropping one")
            return
        try:
            self._show(PluginPopup(plugin.id, plugin.name, plugin.bus_name, note, plugin.alias))
        except Exception:
            log.exception("showing a plugin popup failed")

    # ---- the popup's button -----------------------------------------------------

    def invoke(self, popup: PluginPopup) -> None:
        """The user clicked the popup's action button."""
        if not popup.note.has_action:
            return

        def replied(text) -> None:
            if not isinstance(text, str) or len(text.encode("utf-8", "surrogatepass")) > (
                    MAX_REPLY_BYTES):
                log.info("a plugin answered a popup click with an unusable reply")
                return
            try:
                result = parse_action_result(text)
            except SurfaceError:
                log.info("a plugin answered a popup click with garbage")
                return
            uri = checked_open_uri(
                result.open_uri, plugin_cache_roots(popup.plugin_id, popup.plugin_alias),
                uid=self._uid,
            )
            if result.ok and uri:
                # The answer came from whoever owns the name now (activation):
                # open nothing for a plugin run by another user.
                self._open_if_own(popup.bus_name, uri)
            elif result.open_uri and not uri:
                log.info("refused a plugin open_uri outside http(s) and its cache")

        def failed(error) -> None:
            name = getattr(error, "get_dbus_name", lambda: "")() or type(error).__name__
            log.info("plugin popup action failed: %s", name)

        self._bus.call_async(
            popup.bus_name, OBJECT_PATH, SURFACES_INTERFACE, METHOD_INVOKE_ACTION, "sss",
            (NOTIFY_ITEM_ID, popup.note.action_id, "{}"), replied, failed,
            timeout=ACTION_TIMEOUT_SEC,
        )

    def _open_if_own(self, bus_name: str, uri: str) -> None:
        def checked(uid) -> None:
            if int(uid) != self._uid:
                log.warning("ignoring a popup action answered by another user")
                return
            try:
                self._open_uri(uri)
            except Exception:
                log.info("could not open what the plugin asked for")

        self._bus.call_async(
            *_DBUS, "GetConnectionUnixUser", "s", (bus_name,), checked,
            lambda _error: log.debug("could not check who answered a popup action"),
            timeout=LOOKUP_TIMEOUT_SEC,
        )
