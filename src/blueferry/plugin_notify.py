"""Desktop popups requested by plugins (capability ``notify``, PLUGINS.md 1.2).

A plugin emits ``Notify1.Notify(title, body, icon, action_label, action_id)``
on the session bus. The daemon shows it through its own notification sink,
so the user's policy applies (``none`` silences plugins too, and without
``BLUEFERRY_SHOW_NOTIFICATION_CONTENT`` only the plugin's name is shown).
A click on the popup's button calls the plugin's
``Notify1.InvokeAction("notify", action_id, "{}")``.

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
from blueferry.plugin_api import CAPABILITY_NOTIFY, NOTIFY_INTERFACE, OBJECT_PATH
from blueferry.plugin_api.client import default_cache_roots
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
ACTION_TIMEOUT_SEC = 60.0
LOOKUP_TIMEOUT_SEC = 2.0


@dataclass(frozen=True, slots=True)
class PluginPopup:
    plugin_id: str
    plugin_name: str
    bus_name: str
    note: Notification


def notify_plugins() -> list[PluginManifest]:
    """Enabled manifests with ``notify``; files only, nothing is started."""
    disabled = disabled_plugins()
    found = discover(blueferry_version=__version__)
    return [p for p in found.with_capability(CAPABILITY_NOTIFY) if p.id not in disabled]


def gio_open_uri(uri: str) -> None:
    from gi.repository import Gio

    Gio.AppInfo.launch_default_for_uri_async(uri, None, None, None, None)


class PluginPopups:
    """Watch Notify1.Notify and hand verified popups to ``show``."""

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
        self._match = None

    def start(self) -> None:
        if self._match is not None:
            return
        try:
            self._match = self._bus.add_signal_receiver(
                self._on_notify, dbus_interface=NOTIFY_INTERFACE, signal_name="Notify",
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
        try:
            candidates = list(self._plugins())
        except Exception:
            log.debug("could not read plugin manifests", exc_info=True)
            return
        self._match_owner(str(sender), note, candidates)

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
            self._deliver(plugin, note)

        self._bus.call_async(
            *_DBUS, "GetConnectionUnixUser", "s", (sender,), checked,
            lambda _error: log.debug("could not check a plugin's user"),
            timeout=LOOKUP_TIMEOUT_SEC,
        )

    def _deliver(self, plugin: PluginManifest, note: Notification) -> None:
        now = self._clock()
        window = self._recent.setdefault(plugin.id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= POPUPS_PER_MINUTE:
            log.info("plugin popup rate limit reached; dropping one")
            return
        window.append(now)
        try:
            self._show(PluginPopup(plugin.id, plugin.name, plugin.bus_name, note))
        except Exception:
            log.exception("showing a plugin popup failed")

    # ---- the popup's button -----------------------------------------------------

    def invoke(self, popup: PluginPopup) -> None:
        """The user clicked the popup's action button."""
        if not popup.note.has_action:
            return

        def replied(text) -> None:
            try:
                result = parse_action_result(str(text))
            except SurfaceError:
                log.info("a plugin answered a popup click with garbage")
                return
            uri = checked_open_uri(result.open_uri, default_cache_roots(), uid=self._uid)
            if result.ok and uri:
                try:
                    self._open_uri(uri)
                except Exception:
                    log.info("could not open what the plugin asked for")
            elif result.open_uri and not uri:
                log.info("refused a plugin open_uri outside http(s) and its cache")

        def failed(error) -> None:
            name = getattr(error, "get_dbus_name", lambda: "")() or type(error).__name__
            log.info("plugin popup action failed: %s", name)

        self._bus.call_async(
            popup.bus_name, OBJECT_PATH, NOTIFY_INTERFACE, "InvokeAction", "sss",
            (NOTIFY_ITEM_ID, popup.note.action_id, "{}"), replied, failed,
            timeout=ACTION_TIMEOUT_SEC,
        )
