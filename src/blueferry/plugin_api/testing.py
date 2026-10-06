"""Test harness for plugins and their clients; no bus, no threads.

``inline_service(ServiceClass, manifest, ...)`` builds a service whose worker
and main-loop hand-offs run inline. ``ServiceTransport`` lets a
:class:`~blueferry.plugin_api.client.PluginClient` call that service
directly, through the same validation the real D-Bus transport gets.
``ScriptedTransport`` replays canned replies for client-only tests.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from . import PHOTOS_INTERFACE, PLUGIN_INTERFACE
from .client import PluginError
from .manifest import PluginManifest, parse_manifest
from .service import PluginService


def manifest(
    plugin_id: str = "io.example.photos",
    *,
    capabilities: str = "photos;",
    api_version: int = 1,
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
        self.calls.append((interface, method, tuple(args)))
        if interface not in (PLUGIN_INTERFACE, PHOTOS_INTERFACE):
            raise PluginError("unknown interface")
        handler = getattr(self.service, method)
        if method in ("ListRecent", "FetchOriginal"):
            outcome: dict[str, object] = {}
            handler(
                *args,
                reply=lambda value: outcome.setdefault("reply", value),
                error=lambda error: outcome.setdefault("error", error),
                sender=":1.test",
            )
            if "error" in outcome:
                raise PluginError(str(outcome["error"]))
            return outcome["reply"]
        return handler(*args, sender=":1.test")

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
