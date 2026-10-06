"""Plugin side of the contract: export Plugin1 (and a capability) on D-Bus.

A plugin is its own process with its own session-bus name. Subclass
:class:`PluginService` (or :class:`PhotosService`), implement the hooks and
call :func:`run`. Slow work (network, disk) runs on a worker thread; replies
go back through the GLib main loop, so the plugin keeps answering Status()
while a download runs. The service exits after ``idle_seconds`` without
calls; D-Bus activation starts it again on the next call.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

import dbus
import dbus.service

from . import (
    API_VERSION,
    MAX_RECENT_PHOTOS,
    MAX_REPLY_BYTES,
    OBJECT_PATH,
    PHOTOS_INTERFACE,
    PLUGIN_INTERFACE,
)
from .manifest import PluginManifest

log = logging.getLogger(__name__)

# Per-caller budget; a plugin is local and single-user, this only stops a
# runaway client from turning it into a download loop.
CALLS_PER_MINUTE = 120
Schedule = Callable[[Callable[[], object]], object]


class PluginCallError(dbus.exceptions.DBusException):
    _dbus_error_name = "io.weirdware.BlueFerry.Plugin.Error.Failed"


class RateLimitedError(dbus.exceptions.DBusException):
    _dbus_error_name = "io.weirdware.BlueFerry.Plugin.Error.RateLimited"


def _idle_add(callback: Callable[[], object]) -> object:
    from gi.repository import GLib

    def once() -> bool:
        callback()
        return False

    return GLib.idle_add(once)


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="blueferry-plugin-worker", daemon=True).start()


class PluginService(dbus.service.Object):
    """Plugin1: GetInfo() and Status(); subclasses add capability methods."""

    def __init__(
        self,
        manifest: PluginManifest,
        bus: Any = None,
        *,
        to_main: Schedule = _idle_add,
        start_worker: Callable[[Callable[[], None]], None] = _thread,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(bus, OBJECT_PATH) if bus is not None else super().__init__()
        self.manifest = manifest
        self._to_main = to_main
        self._start_worker = start_worker
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self.last_activity = clock()
        # Calls whose worker has not replied yet; run() never idles out then.
        self.in_flight = 0

    # ---- hooks -----------------------------------------------------------

    def status(self) -> dict[str, object]:
        """``{"state": ok|unconfigured|error|busy, "detail": str, ...}``."""
        return {"state": "ok"}

    # ---- helpers ---------------------------------------------------------

    def admit(self, sender: str | None) -> None:
        """Count one call; raise RateLimitedError over the budget."""
        now = self._clock()
        self.last_activity = now
        window = self._calls[str(sender)]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= CALLS_PER_MINUTE:
            raise RateLimitedError("too many calls")
        window.append(now)

    def run_async(
        self,
        work: Callable[[], str],
        reply: Callable[[str], None],
        error: Callable[[Exception], None],
    ) -> None:
        """Run ``work`` off the main loop and reply on it."""
        self.in_flight += 1

        def finish() -> None:
            self.in_flight -= 1
            self.last_activity = self._clock()

        def worker() -> None:
            try:
                result = work()
                if len(result.encode("utf-8", "surrogatepass")) > MAX_REPLY_BYTES:
                    raise PluginCallError("reply too large")
            except Exception as failure:  # reported to the caller, not raised
                message = str(failure) if isinstance(failure, PluginCallError) else (
                    type(failure).__name__
                )
                log.info("plugin call failed: %s", message)

                def failed() -> None:
                    finish()
                    error(PluginCallError(message))

                self._to_main(failed)
                return

            def done() -> None:
                finish()
                reply(result)

            self._to_main(done)

        self._start_worker(worker)

    # ---- Plugin1 ----------------------------------------------------------

    @dbus.service.method(
        PLUGIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
    )
    def GetInfo(self, sender=None) -> str:
        self.admit(sender)
        return json.dumps({
            "id": self.manifest.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "api_version": API_VERSION,
            "capabilities": list(self.manifest.capabilities),
        })

    @dbus.service.method(
        PLUGIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
    )
    def Status(self, sender=None) -> str:
        self.admit(sender)
        return json.dumps(self.status())


class PhotosService(PluginService):
    """Photos1: ListRecent, FetchOriginal and the content-free Changed()."""

    def list_recent(self, limit: int) -> list[dict[str, object]]:
        """Blocking; runs on a worker thread. Newest first."""
        raise NotImplementedError

    def fetch_original(self, photo_id: str) -> str:
        """Blocking; runs on a worker thread. Return a local path."""
        raise NotImplementedError

    @dbus.service.method(
        PHOTOS_INTERFACE, in_signature="u", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def ListRecent(self, limit, reply, error, sender=None) -> None:
        self.admit(sender)
        bounded = max(1, min(int(limit), MAX_RECENT_PHOTOS))
        self.run_async(lambda: json.dumps(self.list_recent(bounded)), reply, error)

    @dbus.service.method(
        PHOTOS_INTERFACE, in_signature="s", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def FetchOriginal(self, photo_id, reply, error, sender=None) -> None:
        self.admit(sender)
        self.run_async(lambda: str(self.fetch_original(str(photo_id))), reply, error)

    @dbus.service.signal(PHOTOS_INTERFACE, signature="")
    def Changed(self) -> None:
        """Something changed; clients call ListRecent again. No content."""


def run(
    make_service: Callable[[Any], PluginService],
    *,
    idle_seconds: int = 600,
) -> int:
    """Own the plugin's bus name and serve until idle; return an exit code."""
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    service = make_service(bus)
    try:
        name = dbus.service.BusName(
            service.manifest.bus_name, bus, allow_replacement=False,
            replace_existing=False, do_not_queue=True,
        )
    except dbus.exceptions.NameExistsException:
        log.info("%s is already running", service.manifest.bus_name)
        return 0
    loop = GLib.MainLoop()

    def check_idle() -> bool:
        if service.in_flight == 0 and time.monotonic() - service.last_activity > idle_seconds:
            loop.quit()
            return False
        return True

    GLib.timeout_add_seconds(30, check_idle)
    loop.run()
    del name
    return 0
