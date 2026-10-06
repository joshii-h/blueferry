"""Test harness for plugins and their clients; no bus, no threads.

``inline_service(ServiceClass, manifest, ...)`` builds a service whose worker
and main-loop hand-offs run inline. ``ServiceTransport`` lets a
:class:`~blueferry.plugin_api.client.PluginClient` call that service
directly, through the same validation the real D-Bus transport gets.
``ScriptedTransport`` replays canned replies for client-only tests.
``FakeHost`` plays BlueFerry's side of the ApiVersion 1.2 surfaces (card,
share, notify) against a service, with the clients' own validation.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import (
    PHOTOS_INTERFACE,
    PLUGIN_INTERFACE,
    surfaces,
)
from .client import PluginClient, PluginError
from .manifest import PluginManifest, parse_manifest
from .service import PluginService

_INTERFACES = frozenset({PLUGIN_INTERFACE, PHOTOS_INTERFACE})


def manifest(
    plugin_id: str = "io.example.photos",
    *,
    capabilities: str = "photos;",
    api_version: int | str = 1,
    extra: str = "",
) -> PluginManifest:
    return parse_manifest(
        "[BlueFerry Plugin]\n"
        f"Id={plugin_id}\nName=Example\nVersion=1.0\nApiVersion={api_version}\n"
        f"MinBlueFerry=0.1\nCapabilities={capabilities}\n"
        "Homepage=https://example.org/plugin\nExec=example-plugin serve\n" + extra
    )


def inline_service(factory: Callable[..., PluginService], *args: Any, **kwargs: Any):
    kwargs.setdefault("to_main", lambda callback: callback())
    kwargs.setdefault("start_worker", lambda work: work())
    return factory(*args, **kwargs)


class ServiceTransport:
    """Calls a service object in-process; owner is the current user."""

    def __init__(self, service: PluginService, *, uid: int | None = None) -> None:
        self.service = service
        self.uid = os.getuid() if uid is None else uid
        self.calls: list[tuple[str, str, tuple]] = []

    def call(self, bus_name, interface, method, signature, args, timeout) -> object:
        import dbus.exceptions
        from dbus.service import _method_lookup

        self.calls.append((interface, method, tuple(args)))
        if interface not in _INTERFACES:
            raise PluginError("unknown interface")
        # The same lookup dbus-python does for a call on the bus: a member
        # on another interface than the one called is not found.
        try:
            function, _parent = _method_lookup(self.service, method, interface)
        except (AttributeError, dbus.exceptions.DBusException):
            raise PluginError("unknown method") from None
        options = getattr(function, "_dbus_async_callbacks", None)
        try:
            if options:
                outcome: dict[str, object] = {}
                function(
                    self.service, *args,
                    reply=lambda value: outcome.setdefault("reply", value),
                    error=lambda error: outcome.setdefault("error", error),
                    sender=":1.test",
                )
                if "error" in outcome:
                    raise PluginError(str(outcome["error"]))
                return outcome["reply"]
            return function(self.service, *args, sender=":1.test")
        except dbus.exceptions.DBusException as error:
            raise PluginError(str(error.get_dbus_message() or error)) from None

    def owner_uid(self, bus_name: str) -> int | None:
        return self.uid


class ScriptedTransport:
    """Canned replies keyed by method name; values may be callables."""

    def __init__(self, replies: Mapping[str, object], *, uid: int | None = None) -> None:
        self.replies = dict(replies)
        self.uid = os.getuid() if uid is None else uid
        self.calls: list[tuple[str, tuple]] = []

    def call(self, bus_name, interface, method, signature, args, timeout) -> object:
        self.calls.append((method, tuple(args)))
        reply = self.replies.get(method)
        if isinstance(reply, Exception):
            raise reply
        return reply(*args) if callable(reply) else reply

    def owner_uid(self, bus_name: str) -> int | None:
        return self.uid


class FakeHost:
    """BlueFerry's side of the 1.2 surfaces, in-process, for plugin tests.

    ``host = FakeHost(inline_service(MyPlugin, manifest))`` then
    ``host.card_items()``, ``host.invoke(item, action)``,
    ``host.share_targets()``, ``host.send(target, paths)``. Signals are
    captured instead of sent: ``host.card_changes`` counts CardChanged,
    ``host.notifications`` holds every validated Notify, and
    ``host.click(notification)`` presses its action button. Replies pass the
    same validation as in BlueFerry, so text is cut to the limits and an
    ``open_uri`` outside ``cache_root`` comes back as None.
    """

    def __init__(
        self, service: PluginService, *, cache_root: Path | None = None,
    ) -> None:
        self.service = service
        self.card_changes = 0
        self.notifications: list[surfaces.Notification] = []
        self.transport = ServiceTransport(service)
        self.client = PluginClient(
            service.manifest, transport=self.transport,
            cache_root=(lambda: cache_root) if cache_root is not None else None,
        )
        # Instance attributes shadow the signal methods, so the service's
        # emit helpers land here instead of on a bus.
        if hasattr(service, "CardChanged"):
            service.CardChanged = self._card_changed
        if hasattr(service, "Notify"):
            service.Notify = self._notify

    def _card_changed(self) -> None:
        self.card_changes += 1

    def _notify(self, title, body, icon, action_label, action_id) -> None:
        note = surfaces.parse_notification(title, body, icon, action_label, action_id)
        if note is not None:
            self.notifications.append(note)

    def card_items(self) -> list[surfaces.CardItem]:
        return self.client.card_items()

    def invoke(
        self, item_id: str, action_id: str, args: Mapping[str, object] | None = None,
    ) -> surfaces.ActionResult:
        return self.client.invoke_action(item_id, action_id, args)

    def share_targets(self) -> list[surfaces.ShareTarget]:
        return self.client.share_targets()

    def send(self, target_id: str, paths: list[str]) -> surfaces.SendResult:
        return self.client.send_files(target_id, [str(path) for path in paths])

    def click(self, notification: surfaces.Notification) -> surfaces.ActionResult:
        """The user pressed the popup's action button."""
        if not notification.has_action:
            raise PluginError("this popup has no action")
        return self.client.invoke_action(
            surfaces.NOTIFY_ITEM_ID, notification.action_id, {}, notify=True,
        )
