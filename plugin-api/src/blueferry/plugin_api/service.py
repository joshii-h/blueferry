"""Plugin side of the contract: export Plugin1 (and a capability) on D-Bus.

A plugin is its own process with its own session-bus name. Subclass
:class:`PluginService` (or :class:`PhotosService`, and for the ApiVersion 1.2
surfaces :class:`CardService`, :class:`ShareService`, :class:`NotifyService`,
which combine by multiple inheritance), implement the hooks and call
:func:`run`. Slow work (network, disk) runs on a worker thread; replies
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
    API_MINOR,
    API_VERSION,
    CARD_INTERFACE,
    MAX_RECENT_PHOTOS,
    MAX_REPLY_BYTES,
    NOTIFY_INTERFACE,
    OBJECT_PATH,
    PHOTOS_INTERFACE,
    PLUGIN_INTERFACE,
    SHARE_INTERFACE,
    surfaces,
)
from .config import ConfigError, masked, parse_update, validate
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
    """Plugin1: GetInfo(), Status() and the optional GetConfig()/SetConfig()."""

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

    def config_values(self) -> dict[str, object]:
        """Blocking; worker thread. Current settings by manifest key.

        For a ``secret`` field return anything truthy when one is stored
        (``True`` is enough); the base class never sends it out.
        """
        raise PluginCallError("this plugin has no settings")

    def apply_config(self, values: dict[str, object]) -> None:
        """Blocking; worker thread. Store validated settings.

        ``values`` holds every non-secret field and only the secrets the
        user entered anew; keep the stored secret for a missing key. Raise
        :class:`~blueferry.plugin_api.config.ConfigError` to reject one field
        (for example a key the server refuses).
        """
        raise PluginCallError("this plugin has no settings")

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
            "api_minor": API_MINOR,
            "capabilities": list(self.manifest.capabilities),
        })

    @dbus.service.method(
        PLUGIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
    )
    def Status(self, sender=None) -> str:
        self.admit(sender)
        return json.dumps(self.status())


    @dbus.service.method(
        PLUGIN_INTERFACE, in_signature="", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def GetConfig(self, reply, error, sender=None) -> None:
        self.admit(sender)
        self.run_async(self._get_config, reply, error)

    @dbus.service.method(
        PLUGIN_INTERFACE, in_signature="s", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def SetConfig(self, update, reply, error, sender=None) -> None:
        self.admit(sender)
        text = str(update)
        self.run_async(lambda: self._set_config(text), reply, error)

    def _fields(self):
        if not self.manifest.config:
            raise PluginCallError("this plugin has no settings")
        return self.manifest.config

    def _get_config(self) -> str:
        fields = self._fields()
        return json.dumps({"values": masked(fields, self.config_values())})

    def _set_config(self, text: str) -> str:
        fields = self._fields()
        try:
            update = parse_update(text)
        except ConfigError as failure:
            return json.dumps({"ok": False, "errors": {failure.field: failure.message}})
        values, errors = validate(fields, self.config_values(), update)
        if errors:
            return json.dumps({"ok": False, "errors": errors})
        try:
            self.apply_config(values)
        except ConfigError as failure:
            return json.dumps({"ok": False, "errors": {failure.field: failure.message}})
        log.info("plugin settings saved (%d fields)", len(values))
        return json.dumps({"ok": True})


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


def _action_args(text: str) -> dict[str, object]:
    if len(text.encode("utf-8", "surrogatepass")) > surfaces.MAX_ARGS_BYTES:
        raise PluginCallError("action arguments are too large")
    try:
        value = json.loads(text or "{}")
    except ValueError:
        raise PluginCallError("action arguments are not JSON") from None
    if not isinstance(value, dict):
        raise PluginCallError("action arguments must be a JSON object")
    return value


class _ActionMixin(PluginService):
    def invoke_action(
        self, item_id: str, action_id: str, args: dict[str, object],
    ) -> surfaces.ActionResult | dict[str, object]:
        """Blocking; worker thread. A click on a card or popup button.

        ``item_id`` is the card item's id, or ``"notify"`` for the action
        button of a popup sent with :meth:`NotifyService.emit_notify`.
        """
        raise PluginCallError("this plugin has no actions")

    def _invoke(self, item_id: str, action_id: str, args_text: str) -> str:
        if not surfaces.valid_id(item_id) or not surfaces.valid_id(action_id):
            raise PluginCallError("not an action id")
        args = _action_args(args_text)
        return surfaces.result_json(self.invoke_action(item_id, action_id, args))


class CardService(_ActionMixin):
    """Card1: items for the phone card's "From Plugins" section.

    Override :meth:`card_items` and :meth:`invoke_action`; call
    :meth:`emit_card_changed` (from any thread) when the items change.
    """

    def card_items(self) -> list[surfaces.CardItem] | list[dict[str, object]]:
        """Blocking; worker thread. At most 8 items, 3 actions each."""
        return []

    def emit_card_changed(self) -> None:
        """Ask BlueFerry to fetch the items again; safe from any thread."""
        self._to_main(self.CardChanged)

    @dbus.service.method(
        CARD_INTERFACE, in_signature="", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def GetCardItems(self, reply, error, sender=None) -> None:
        self.admit(sender)
        self.run_async(lambda: surfaces.card_items_json(self.card_items()), reply, error)

    @dbus.service.method(
        CARD_INTERFACE, in_signature="sss", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def InvokeAction(self, item_id, action_id, args, reply, error, sender=None) -> None:
        self.admit(sender)
        item, action, text = str(item_id), str(action_id), str(args)
        self.run_async(lambda: self._invoke(item, action, text), reply, error)

    @dbus.service.signal(CARD_INTERFACE, signature="")
    def CardChanged(self) -> None:
        """The card items changed; BlueFerry calls GetCardItems. No content."""


class ShareService(PluginService):
    """Share1: destinations for BlueFerry's "Send to…"."""

    def share_targets(self) -> list[surfaces.ShareTarget] | list[dict[str, object]]:
        """Blocking; worker thread."""
        return []

    def send_files(
        self, target_id: str, paths: list[str],
    ) -> surfaces.SendResult | dict[str, object]:
        """Blocking; worker thread. Start the transfer and return quickly;
        report a long one as a card item (and :meth:`emit_card_changed`)."""
        raise PluginCallError("this plugin cannot send files")

    @dbus.service.method(
        SHARE_INTERFACE, in_signature="", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def ShareTargets(self, reply, error, sender=None) -> None:
        self.admit(sender)
        self.run_async(lambda: surfaces.share_targets_json(self.share_targets()), reply, error)

    @dbus.service.method(
        SHARE_INTERFACE, in_signature="sas", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def SendFiles(self, target_id, paths, reply, error, sender=None) -> None:
        self.admit(sender)
        target = str(target_id)
        files = [str(path) for path in list(paths)[: surfaces.MAX_SHARE_FILES + 1]]

        def work() -> str:
            if not surfaces.valid_id(target):
                raise PluginCallError("not a share target")
            if not files or len(files) > surfaces.MAX_SHARE_FILES:
                raise PluginCallError("send between 1 and 64 files")
            return surfaces.result_json(self.send_files(target, files))

        self.run_async(work, reply, error)


class NotifyService(_ActionMixin):
    """Notify1: desktop popups shown through BlueFerry's notification policy."""

    def emit_notify(
        self, title: str, body: str, icon: str = "", action_label: str = "",
        action_id: str = "",
    ) -> None:
        """Show a popup; a click on the button calls ``invoke_action("notify",
        action_id, {})``. Safe from any thread. Keep personal data out of it
        when you can: the signal is visible to the user's session bus."""
        note = surfaces.parse_notification(title, body, icon, action_label, action_id)
        if note is None:
            return
        self._to_main(lambda: self.Notify(
            note.title, note.body, note.icon, note.action_label, note.action_id,
        ))

    @dbus.service.method(
        NOTIFY_INTERFACE, in_signature="sss", out_signature="s",
        async_callbacks=("reply", "error"), sender_keyword="sender",
    )
    def InvokeAction(self, item_id, action_id, args, reply, error, sender=None) -> None:
        self.admit(sender)
        item, action, text = str(item_id), str(action_id), str(args)
        self.run_async(lambda: self._invoke(item, action, text), reply, error)

    @dbus.service.signal(NOTIFY_INTERFACE, signature="sssss")
    def Notify(self, title, body, icon, action_label, action_id) -> None:
        """A popup request; BlueFerry applies its notification policy."""


def emit_card_changed(service: CardService) -> None:
    """Module-level form of :meth:`CardService.emit_card_changed`."""
    service.emit_card_changed()


def emit_notify(
    service: NotifyService, title: str, body: str, icon: str = "",
    action_label: str = "", action_id: str = "",
) -> None:
    """Module-level form of :meth:`NotifyService.emit_notify`."""
    service.emit_notify(title, body, icon, action_label, action_id)


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
