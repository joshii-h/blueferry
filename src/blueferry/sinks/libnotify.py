"""Desktop notification sink via org.freedesktop.Notifications.

Body format: title = display sender (contact name or phone number),
             body  = SMS text (truncated at ~280 chars to avoid huge popups).

Popups request a finite lifetime. If the user explicitly dismisses an SMS
popup before it expires, we defer marking that message read on the iPhone so
ANCS has time to deliver its group metadata (unless
BLUEFERRY_MARK_READ_ON_DISMISS is false). If the iPhone marks it read while
the popup is visible, we close it early.

Read-state sync:
  Linux dismiss → MAP Message1.Properties.Set(Read=true)  → iPhone marks read
  iPhone reads  → MAP PropertiesChanged(Read=true)         → we close popup

Empirical on iOS 26.5: both directions work. iPhone propagates Read=true
back over MAP within a few seconds of opening the Messages app.

ANCS notification actions (opt-in, BLUEFERRY_ANCS_ACTIONS):
  iPhone offers Accept/Decline/Clear/... → popup action buttons
  user clicks one button                 → PerformNotificationAction
Dismissing or expiring an ANCS popup never touches the iPhone, and nothing is
invoked without an explicit click on the labelled button.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Protocol

import dbus
import dbus.exceptions

from blueferry import config
from blueferry.ancs.events import AncsEvent
from blueferry.bus import get_session_bus
from blueferry.call_history import MAX_INDIVIDUAL_MISSED_CALL_POPUPS, MissedCallNotice
from blueferry.calls.model import CallEvent
from blueferry.client_activation import activation_argv, select_client
from blueferry.events import SmsEvent
from blueferry.limits import MAX_ANCS_ACTION_POPUPS, MAX_DESKTOP_MESSAGE_TRACKERS
from blueferry.notification_open import helper_argv
from blueferry.notification_policy import (
    ALL_NOTIFICATIONS,
    DEFAULT_NOTIFICATION_POLICY,
    NO_NOTIFICATIONS,
)
from blueferry.text_safety import terminal_text
from blueferry.time_display import format_message_timestamp


class _SignalMatch(Protocol):
    def remove(self) -> object: ...

log = logging.getLogger(__name__)

_APP_NAME = "BlueFerry"
_BODY_LIMIT = 280
_MESSAGE_EXPIRE_MS = config.NOTIFICATION_TIMEOUT_MS
# ANCS mirrors ordinary iPhone app/system notifications. Unlike MAP messages,
# they have no desktop-to-phone read-state path, so keeping every popup around
# indefinitely only creates notification-center clutter.
_ANCS_EXPIRE_MS = config.NOTIFICATION_TIMEOUT_MS
# Popups with iPhone action buttons (a ringing call, an invitation) need to
# stay long enough to be answered.
_ANCS_ACTION_EXPIRE_MS = config.ANCS_ACTION_TIMEOUT_MS

# NotificationClosed reason codes (org.freedesktop.Notifications spec):
#   1 = expired (timeout)
#   2 = dismissed by user
#   3 = CloseNotification() called programmatically (e.g. by us on iPhone-read)
#   4 = undefined / reserved
#
# We mark-read only on dismissed-by-user. Reason 3 = we're already closing
# because the iPhone marked it read (so we'd be in a write-self-write loop).
# Reason 1 is the normal finite-timeout path and must not mark the phone read.
_REASON_DISMISSED = 2
# Minimum spacing between two launches of configured notification targets.
_OPEN_TARGET_INTERVAL_S = 1.0

# Incoming-call popups stay until the call stops ringing; the sink closes
# them itself when the call is answered, declined, or disappears.
_CALL_EXPIRE_MS = 0
_CALL_ACTIONS = ("answer", "decline")
# Notification action keys for ANCS actions. They never collide with the
# message popup's "default" action, so a click on the popup body cannot run an
# iPhone action.
_ANCS_POSITIVE_ACTION = "ancs-positive"
_ANCS_NEGATIVE_ACTION = "ancs-negative"
# The single button of a plugin popup (PLUGINS.md, capability notify).
_PLUGIN_ACTION = "plugin-action"
_PLUGIN_ICON = "preferences-plugin"
_MAX_PLUGIN_POPUPS = 32
# Action labels are plain strings, but some notification servers interpret
# markup in them. Drop markup-significant characters instead of escaping, so a
# server that does not parse markup shows no literal entities.
_LABEL_TAG_RE = re.compile(r"<[^>]*>")
_LABEL_MARKUP_RE = re.compile(r"[<>&]")
_ANCS_ACTION_FEEDBACK = {
    "unavailable": "The notification is no longer available on the iPhone.",
    "disconnected": "The iPhone is not connected.",
    "busy": "Another iPhone action is still in progress.",
    "rejected": "The iPhone could not complete the action.",
    "unsupported": "The iPhone does not support this action.",
    "failed": "The action could not be sent to the iPhone.",
}


def _notification_hints(handle: str) -> dict[str, object]:
    hints: dict[str, object] = {"urgency": dbus.Byte(1)}
    if not handle:
        return hints
    client = select_client(get_session_bus().list_names())
    if client is not None:
        hints["desktop-entry"] = client.desktop_id
    # Omarchy's notification shell prefers this JSON argv over a live
    # libnotify action, and it survives a shell restart.
    hints["omarchy-exec-argv"] = json.dumps(activation_argv(handle))
    return hints


class LibnotifySink:
    name = "libnotify"
    # New sinks fail closed in EventDispatcher. This one accepts system ANCS
    # only to create an immediate transient popup; it retains no event data.
    accepts_system_ancs = True

    def __init__(
        self,
        *,
        defer_mark_read: Callable[[str], None],
        notification_policy=None,
        contacts_only_notifications=None,
        on_open_message=None,
        on_call_action=None,
        contact_photo: Callable[[str | None], str | None] | None = None,
        on_ancs_action=None,
        open_target=None,
        on_open_target=None,
        on_plugin_action=None,
    ) -> None:
        self._defer_mark_read = defer_mark_read
        # PluginPopup -> None: a click on a plugin popup's button.
        self._on_plugin_action = on_plugin_action
        # desktop notification id -> PluginPopup with an action button.
        self._plugin_popups: dict[int, object] = {}
        # (call_id, "answer" | "decline") from an incoming-call popup button.
        self._on_call_action = on_call_action
        # notification_id <-> call_id for ringing-call popups.
        self._call_notifications: dict[int, str] = {}
        self._call_popups: dict[str, int] = {}
        self._contact_photo = contact_photo
        self._notification_policy = notification_policy
        self._contacts_only_notifications = contacts_only_notifications
        self._on_open_message = on_open_message
        # (uid, positive, on_result) -> dispatched; None disables actions.
        self._on_ancs_action = on_ancs_action
        # desktop notification id -> ANCS uid for popups with iPhone actions.
        self._ancs_actions: dict[int, int] = {}
        # desktop notification id -> ANCS uid for every ANCS popup of the
        # current session, so a removal on the iPhone can close it.
        self._ancs_popups: dict[int, int] = {}
        # Resolves an ANCS bundle ID to the user's click rule (or None).
        self._open_target = open_target
        self._on_open_target = on_open_target
        self._notif = dbus.Interface(
            get_session_bus().get_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            ),
            "org.freedesktop.Notifications",
        )
        # notification_id (uint32 from Notify) -> Message1 DBus path
        self._pending: dict[int, str] = {}
        # notification_id -> opaque MAP handle.  Handles let clients locate
        # the message in their private thread snapshot without broadcasting a
        # phone number or message body on the session bus.
        self._open_messages: dict[int, str] = {}
        self._activation_tokens: dict[int, str] = {}
        # notification_id -> ANCS bundle ID with a configured click rule. The
        # rule itself is looked up again on click so a removed rule is final.
        self._open_apps: dict[int, str] = {}
        self._last_open_target = float("-inf")
        # notification_id -> SignalMatch for the per-Message1 PropertiesChanged sub
        self._msg_subs: dict[int, _SignalMatch] = {}

        # Listen for any of our notifications closing (dismissed, expired,
        # or programmatically closed).
        self._match = self._notif.connect_to_signal(
            "NotificationClosed", self._on_closed,
        )
        self._action_match = self._notif.connect_to_signal(
            "ActionInvoked", self._on_action,
        )
        self._token_match = self._notif.connect_to_signal(
            "ActivationToken", self._on_activation_token,
        )
        log.info("libnotify sink ready (expiring + bidirectional read-sync)")

    def close(self) -> None:
        """Release signal watches before a notification-daemon replacement."""
        for attribute in ("_match", "_action_match", "_token_match"):
            match = getattr(self, attribute, None)
            if match is not None:
                try:
                    match.remove()
                except Exception:
                    log.debug("could not remove libnotify signal watch", exc_info=True)
                setattr(self, attribute, None)
        for subscription in self._msg_subs.values():
            try:
                subscription.remove()
            except Exception:
                log.debug("could not remove message read-state watch", exc_info=True)
        self._msg_subs.clear()
        self._pending.clear()
        self._open_messages.clear()
        getattr(self, "_open_apps", {}).clear()
        getattr(self, "_activation_tokens", {}).clear()
        getattr(self, "_call_notifications", {}).clear()
        getattr(self, "_call_popups", {}).clear()
        getattr(self, "_ancs_actions", {}).clear()
        getattr(self, "_plugin_popups", {}).clear()

    def _policy(self) -> str:
        provider = getattr(self, "_notification_policy", None)
        return (
            str(provider())
            if provider is not None
            else DEFAULT_NOTIFICATION_POLICY
        )

    def _contacts_only(self) -> bool:
        provider = getattr(self, "_contacts_only_notifications", None)
        return bool(provider()) if provider is not None else False

    def handle(self, event: SmsEvent) -> None:
        # Don't pop a desktop notification for a message we ourselves sent.
        if event.kind == "sms_sent":
            return
        if self._policy() == NO_NOTIFICATIONS:
            return
        if self._contacts_only() and not getattr(event, "contact_name", None):
            return
        title = f"\U0001f4ac {event.display_sender}"
        body = (event.body or "").strip()
        if not config.SHOW_NOTIFICATION_CONTENT:
            body = "New message"
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        # The freedesktop body field accepts markup. Message text is remote,
        # untrusted input, so escape it before handing it to the shell.
        title = escape(terminal_text(title).replace("\n", " "))
        body = escape(terminal_text(body))
        try:
            # A deliberate dismissal is propagated as mark-read. Expiration
            # is reason=1 and therefore leaves the iPhone's read state alone.
            handle = str(getattr(event, "handle", "") or "")
            actions = ["default", "Open conversation"] if handle else []
            hints = _notification_hints(handle)
            photo = self._photo_uri(event)
            if photo:
                # The notification server decodes the image, not the daemon.
                hints["image-path"] = photo
            nid = int(self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "phone-symbolic",
                title,
                body,
                dbus.Array(actions, signature="s"),
                dbus.Dictionary(hints, signature="sv"),
                dbus.Int32(_MESSAGE_EXPIRE_MS),
            ))
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify failed: %s", e.get_dbus_name())
            return

        if handle:
            if not hasattr(self, "_open_messages"):
                self._open_messages = {}
            self._open_messages[nid] = handle

        if event.message_path:
            self._pending[nid] = event.message_path
            # Subscribe to PropertiesChanged on this specific Message1 path
            # so we get notified if iOS marks it read.
            try:
                self._msg_subs[nid] = get_session_bus().add_signal_receiver(
                    lambda iface, changed, _inv, nid=nid:
                        self._on_msg_props(nid, iface, changed),
                    dbus_interface="org.freedesktop.DBus.Properties",
                    signal_name="PropertiesChanged",
                    bus_name="org.bluez.obex",
                    path=event.message_path,
                )
            except dbus.exceptions.DBusException as error:
                self._pending.pop(nid, None)
                log.warning(
                    "could not watch message read state: %s",
                    error.get_dbus_name(),
                )
        self._prune_trackers()

    def _photo_uri(self, event: SmsEvent) -> str:
        """``file://`` URI of the sender's volatile avatar copy, or ``""``."""
        provider = getattr(self, "_contact_photo", None)
        if provider is None:
            return ""
        try:
            path = provider(event.sender_address)
        except Exception:
            log.debug("contact photo lookup failed", exc_info=True)
            return ""
        return Path(path).as_uri() if path else ""

    def _prune_trackers(self) -> None:
        """Bound read-state subscriptions if close signals never arrive."""
        open_messages = getattr(self, "_open_messages", {})
        open_apps = getattr(self, "_open_apps", {})
        while (
            len(set(self._pending) | set(open_messages) | set(open_apps))
            > MAX_DESKTOP_MESSAGE_TRACKERS
        ):
            tracked = open_messages or open_apps or self._pending
            oldest = next(iter(tracked))
            self._pending.pop(oldest, None)
            open_messages.pop(oldest, None)
            open_apps.pop(oldest, None)
            getattr(self, "_activation_tokens", {}).pop(oldest, None)
            subscription = self._msg_subs.pop(oldest, None)
            if subscription is not None:
                try:
                    subscription.remove()
                except Exception:
                    log.debug("could not remove stale message watch", exc_info=True)
            self._close_async(oldest)

    # ---- ANCS events (per-app notifications) ----------------------------

    def handle_ancs(self, event: AncsEvent) -> None:
        if self._policy() != ALL_NOTIFICATIONS:
            return
        # Messages already arrive through MAP. The ANCS copy is retained for
        # group metadata but never creates a second desktop popup.
        if event.app_id == "com.apple.MobileSMS":
            return
        # Title: "📱 AppName" or "📱 com.bundle.id" if no name yet
        app = event.app_name or event.app_id or "Notification"
        title = f"\U0001f4f1 {app}"
        # Body: prefer Title field for headline, then Message
        body_parts = [p for p in (event.title, event.body) if p]
        body = " — ".join(body_parts) if body_parts else ""
        if not config.SHOW_NOTIFICATION_CONTENT:
            body = "New iPhone notification"
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        title = escape(terminal_text(title).replace("\n", " "))
        body = escape(terminal_text(body))
        ancs_buttons = (
            self._ancs_action_buttons(event)
            if getattr(event, "has_actions", False)
            else []
        )
        app_id = event.app_id if isinstance(event.app_id, str) else ""
        # Only a user-configured rule adds a click action; without one the
        # popup stays exactly as before. The action label is fixed text.
        open_target = self._resolve_open_target(app_id)
        clickable = open_target is not None
        actions = ancs_buttons + (["default", "Open"] if clickable else [])
        hints: dict[str, object] = {
            "urgency": dbus.Byte(1),
            # Plasma can retain an expired notification in history.
            # ANCS events already live in BlueFerry's own feed, so
            # explicitly bypass desktop notification persistence.
            "transient": dbus.Boolean(True),
        }
        if open_target is not None:
            # Like message popups, give Omarchy's shell (which prefers an
            # argv over a live action) the same fixed helper command.
            hints["omarchy-exec-argv"] = json.dumps(helper_argv(open_target))
        try:
            # No mark-read sync exists for ANCS, so use a normal finite popup
            # lifetime. The event remains available in private SQLite history.
            nid = self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "phone-symbolic",
                title,
                body,
                dbus.Array(actions, signature="s"),
                dbus.Dictionary(hints, signature="sv"),
                dbus.Int32(_ANCS_ACTION_EXPIRE_MS if ancs_buttons else _ANCS_EXPIRE_MS),
            )
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify (ANCS) failed: %s", e.get_dbus_name())
            return
        if ancs_buttons:
            self._track_ancs_actions(int(nid), int(event.notification_id))
        self._track_ancs_popup(int(nid), int(getattr(event, "notification_id", 0) or 0))
        if clickable:
            if not hasattr(self, "_open_apps"):
                self._open_apps = {}
            self._open_apps[int(nid)] = app_id
            self._prune_trackers()

    def _ancs_actions_enabled(self) -> bool:
        # Labels are app-defined ("Pay CHF 50 to Bob"), so they count as
        # notification content and follow BLUEFERRY_SHOW_NOTIFICATION_CONTENT.
        return (
            config.ancs_actions_active()
            and getattr(self, "_on_ancs_action", None) is not None
        )

    def _ancs_action_buttons(self, event: AncsEvent) -> list[str]:
        """Return freedesktop action pairs for the iPhone-offered actions."""
        if not self._ancs_actions_enabled():
            return []
        actions: list[str] = []
        for key, label in (
            (_ANCS_POSITIVE_ACTION, getattr(event, "positive_action_label", "")),
            (_ANCS_NEGATIVE_ACTION, getattr(event, "negative_action_label", "")),
        ):
            # Action labels are plain text in the spec, but some servers
            # render them loosely; apply the same display sanitizing.
            text = terminal_text(str(label or "")).replace("\n", " ")
            text = _LABEL_MARKUP_RE.sub("", _LABEL_TAG_RE.sub("", text))
            text = " ".join(text.split())
            if text:
                actions += [key, text]
        return actions

    def _track_ancs_actions(self, nid: int, uid: int) -> None:
        tracked = getattr(self, "_ancs_actions", None)
        if tracked is None:
            tracked = self._ancs_actions = {}
        tracked.pop(nid, None)
        tracked[nid] = uid
        while len(tracked) > MAX_ANCS_ACTION_POPUPS:
            tracked.pop(next(iter(tracked)))

    def close_ancs_notification(self, uid: int) -> None:
        """Close a popup whose iPhone notification was removed on the phone."""
        tracked = getattr(self, "_ancs_actions", {})
        for nid in [nid for nid, value in tracked.items() if value == uid]:
            tracked.pop(nid, None)
            self._close_async(nid)

    def _track_ancs_popup(self, nid: int, uid: int) -> None:
        tracked = getattr(self, "_ancs_popups", None)
        if tracked is None:
            tracked = self._ancs_popups = {}
        tracked.pop(nid, None)
        tracked[nid] = uid
        while len(tracked) > MAX_ANCS_ACTION_POPUPS:
            tracked.pop(next(iter(tracked)))

    def close_removed_ancs_popup(self, uid: int) -> None:
        """Mirror a removal on the iPhone: close every popup for this UID."""
        tracked = getattr(self, "_ancs_popups", {})
        actions = getattr(self, "_ancs_actions", {})
        for nid in [nid for nid, value in tracked.items() if value == uid]:
            tracked.pop(nid, None)
            actions.pop(nid, None)
            self._close_async(nid)

    def forget_ancs_popups(self) -> None:
        """The ANCS session ended; its UIDs may come back for other popups.

        The popups themselves stay until they expire: their notifications
        may still exist on the iPhone.
        """
        getattr(self, "_ancs_popups", {}).clear()

    def close_all_ancs_notifications(self) -> None:
        """Retire every action popup when the ANCS session (and UIDs) reset.

        A new session may reuse a UID, so an old button must never stay
        wired to it.
        """
        tracked = getattr(self, "_ancs_actions", {})
        stale = list(tracked)
        tracked.clear()
        for nid in stale:
            self._close_async(nid)

    def _close_async(self, nid: int) -> None:
        def failed(error) -> None:
            name = getattr(error, "get_dbus_name", lambda: None)()
            log.debug("CloseNotification(%d): %s", nid, name or type(error).__name__)

        try:
            self._notif.CloseNotification(
                dbus.UInt32(nid),
                reply_handler=lambda *_args: None,
                error_handler=failed,
            )
        except dbus.exceptions.DBusException as error:
            failed(error)

    def _invoke_ancs_action(self, nid: int, action: str) -> None:
        # Pop first: a notification's action is single-use even if the server
        # delivers ActionInvoked twice.
        uid = getattr(self, "_ancs_actions", {}).pop(nid, None)
        callback = getattr(self, "_on_ancs_action", None)
        if uid is None or callback is None or not config.ancs_actions_active():
            return
        positive = action == _ANCS_POSITIVE_ACTION
        try:
            callback(uid, positive, self._ancs_action_result)
        except Exception:
            log.exception("ANCS action callback raised")

    def _ancs_action_result(self, result: str) -> None:
        """Tell the user when a clicked iPhone action did not go through."""
        message = _ANCS_ACTION_FEEDBACK.get(result)
        if message is None:
            return
        try:
            self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "phone-symbolic",
                "\U0001f4f1 iPhone action not completed",
                escape(message),
                dbus.Array([], signature="s"),
                dbus.Dictionary({
                    "urgency": dbus.Byte(1),
                    "transient": dbus.Boolean(True),
                }, signature="sv"),
                dbus.Int32(_ANCS_EXPIRE_MS),
            )
        except dbus.exceptions.DBusException as e:
            log.debug("libnotify action feedback failed: %s", e.get_dbus_name())

    # ---- optional phone calls ---------------------------------------------

    def _call_maps(self) -> tuple[dict[int, str], dict[str, int]]:
        if not hasattr(self, "_call_notifications"):
            self._call_notifications = {}
            self._call_popups = {}
        return self._call_notifications, self._call_popups

    def handle_call(self, event: CallEvent) -> None:
        """Show a ringing call with Answer/Decline; close it once it stops."""
        record = event.call
        notifications, popups = self._call_maps()
        if event.kind == "call_ended" or not record.ringing:
            nid = popups.pop(record.call_id, None)
            if nid is None:
                return
            notifications.pop(nid, None)
            self._close_async(nid)
            return
        if record.call_id in popups or self._policy() == NO_NOTIFICATIONS:
            return
        heading = "Call waiting" if record.state == "waiting" else "Incoming call"
        title = escape(terminal_text(f"\U0001f4de {heading}").replace("\n", " "))
        if config.SHOW_NOTIFICATION_CONTENT:
            peer = record.display_peer
            if record.number and peer != record.number:
                peer = f"{peer}\n{record.number}"
            body = escape(terminal_text(peer))
        else:
            # Like message popups, hidden content keeps the caller off screen.
            body = heading
        # contacts_only deliberately does not apply: a call from an unknown
        # number still needs a chance to be answered or declined.
        actions: list[str] = []
        if getattr(self, "_on_call_action", None) is not None:
            actions = ["answer", "Answer", "decline", "Decline"]
        try:
            nid = int(self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "call-start",
                title,
                body,
                dbus.Array(actions, signature="s"),
                dbus.Dictionary({
                    "urgency": dbus.Byte(2),
                    # The call itself is the record; do not keep stale
                    # "incoming call" entries in the notification history.
                    "transient": dbus.Boolean(True),
                }, signature="sv"),
                dbus.Int32(_CALL_EXPIRE_MS),
            ))
        except dbus.exceptions.DBusException as error:
            log.error("libnotify Notify (call) failed: %s", error.get_dbus_name())
            return
        notifications[nid] = record.call_id
        popups[record.call_id] = nid

    def handle_phone_battery_low(self, percent: int) -> None:
        """One desktop warning per discharge cycle (the daemon decides when)."""
        if self._policy() == NO_NOTIFICATIONS:
            return
        level = max(0, min(100, int(percent)))
        try:
            self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "battery-caution",
                "\U0001f50b iPhone battery low",
                f"About {level} % left (the phone reports 20 % steps).",
                dbus.Array([], signature="s"),
                dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
                dbus.Int32(config.NOTIFICATION_TIMEOUT_MS),
            )
        except dbus.exceptions.DBusException as error:
            log.error("libnotify Notify (battery) failed: %s", error.get_dbus_name())

    # ---- plugin popups (capability notify) --------------------------------

    def handle_plugin_notification(self, popup) -> None:
        """A verified Plugin1.Notify popup from a plugin, under the user's policy.

        Like the battery warning it shows under ``messages`` and ``all``;
        ``none`` silences it. Without SHOW_NOTIFICATION_CONTENT only the
        plugin's name appears. Text is plain: escaped for the server.
        """
        if self._policy() == NO_NOTIFICATIONS:
            return
        note = popup.note
        if config.SHOW_NOTIFICATION_CONTENT:
            title = note.title or popup.plugin_name
            body = note.body
        else:
            title = popup.plugin_name
            body = "New notification"
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        title = escape(terminal_text(title).replace("\n", " "))
        body = escape(terminal_text(body))
        actions: list[str] = []
        callback = getattr(self, "_on_plugin_action", None)
        if note.has_action and callback is not None:
            # The label is plugin text too: neutral while content is hidden.
            label = _LABEL_MARKUP_RE.sub("", _LABEL_TAG_RE.sub("", note.action_label))
            if not config.SHOW_NOTIFICATION_CONTENT:
                label = "Open"
            actions = [_PLUGIN_ACTION, label or "Open"]

        def shown(nid) -> None:
            if not actions:
                return
            popups = self._plugin_popups
            popups[int(nid)] = popup
            while len(popups) > _MAX_PLUGIN_POPUPS:
                popups.pop(next(iter(popups)))

        def failed(error) -> None:
            name = getattr(error, "get_dbus_name", lambda: None)()
            log.error("libnotify Notify (plugin) failed: %s", name or type(error).__name__)

        try:
            self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                note.icon or _PLUGIN_ICON,
                title,
                body,
                dbus.Array(actions, signature="s"),
                dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
                dbus.Int32(_MESSAGE_EXPIRE_MS),
                reply_handler=shown,
                error_handler=failed,
            )
        except dbus.exceptions.DBusException as error:
            failed(error)

    def _invoke_plugin_action(self, nid: int) -> None:
        # Single-use, like ANCS actions.
        popup = getattr(self, "_plugin_popups", {}).pop(nid, None)
        callback = getattr(self, "_on_plugin_action", None)
        if popup is None or callback is None:
            return
        try:
            callback(popup)
        except Exception:
            log.exception("plugin popup action callback raised")

    # ---- missed calls (PBAP call history) --------------------------------

    def handle_missed_calls(
        self,
        notices: list[MissedCallNotice],
        *,
        individual_limit: int = MAX_INDIVIDUAL_MISSED_CALL_POPUPS,
    ) -> None:
        """Announce newly missed calls; a burst collapses into one summary.

        Deliberately shown under the default ``messages`` policy, not only
        ``all``: a missed call is person-to-person communication like a
        message, and ``BLUEFERRY_CALL_HISTORY_ENABLED`` plus
        ``BLUEFERRY_MISSED_CALL_NOTIFICATIONS`` are the user's explicit
        consent. ``none`` and contacts-only still apply.
        """
        if self._policy() == NO_NOTIFICATIONS:
            return
        if self._contacts_only():
            notices = [notice for notice in notices if notice.known_contact]
        if not notices:
            return
        if len(notices) > individual_limit:
            title = f"\U0001f4de {len(notices)} missed calls"
            if config.SHOW_NOTIFICATION_CONTENT:
                callers = list(dict.fromkeys(
                    notice.caller or "Unknown caller" for notice in notices
                ))
                body = ", ".join(callers)
            else:
                body = "Missed calls on your iPhone"
            self._notify_missed_call(title, body)
            return
        for notice in notices:
            if config.SHOW_NOTIFICATION_CONTENT:
                title = f"\U0001f4de {notice.caller or 'Unknown caller'}"
                when = format_message_timestamp(notice.occurred_at.isoformat())
                body = f"Missed call · {when}" if when else "Missed call"
            else:
                title = "\U0001f4de Missed call"
                body = "Missed call on your iPhone"
            self._notify_missed_call(title, body)

    def _notify_missed_call(self, title: str, body: str) -> None:
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT - 1] + "…"
        # Caller names and numbers come from the phone: escape like SMS text.
        title = escape(terminal_text(title).replace("\n", " "))
        body = escape(terminal_text(body))
        try:
            self._notif.Notify(
                _APP_NAME,
                dbus.UInt32(0),
                "call-missed-symbolic",
                title,
                body,
                dbus.Array([], signature="s"),
                dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
                dbus.Int32(_MESSAGE_EXPIRE_MS),
            )
        except dbus.exceptions.DBusException as e:
            log.error("libnotify Notify (missed call) failed: %s", e.get_dbus_name())

    def _resolve_open_target(self, app_id: str):
        provider = getattr(self, "_open_target", None)
        if provider is None or not app_id:
            return None
        try:
            return provider(app_id)
        except Exception:
            log.debug("could not resolve a notification click rule", exc_info=True)
            return None

    # ---- iPhone marks read → close our popup ----------------------------

    def _on_msg_props(self, nid: int, iface: str, changed) -> None:
        if iface != "org.bluez.obex.Message1":
            return
        # Look for Read going True. Some BlueZ versions send Status instead.
        read_now = (
            bool(changed.get("Read", False))
            or str(changed.get("Status", "")).lower() in ("read", "complete")
        )
        if not read_now:
            return
        if nid not in self._pending:
            return  # already closed/handled
        log.info("iPhone marked message read — closing popup %d", nid)
        self._close_async(nid)
        # _on_closed will clean up the dict + signal match (reason=3)

    # ---- Linux user dismisses → mark-read on iPhone ----------------------

    def _on_activation_token(self, nid, token) -> None:
        """The notification server supplies a single-use token before the action."""
        try:
            nid_i = int(nid)
        except (TypeError, ValueError):
            return
        tracked = nid_i in self._open_messages or nid_i in getattr(self, "_open_apps", {})
        if tracked and len(str(token)) <= 4096:
            self._activation_tokens[nid_i] = str(token)

    def _on_action(self, nid, action) -> None:
        """Route a notification click to one client, starting it if necessary."""
        try:
            nid_i = int(nid)
        except (TypeError, ValueError):
            return
        call_id = getattr(self, "_call_notifications", {}).get(nid_i)
        if call_id is not None:
            callback = getattr(self, "_on_call_action", None)
            if str(action) in _CALL_ACTIONS and callback is not None:
                callback(call_id, str(action))
            return
        if str(action) in (_ANCS_POSITIVE_ACTION, _ANCS_NEGATIVE_ACTION):
            self._invoke_ancs_action(nid_i, str(action))
            return
        if str(action) == _PLUGIN_ACTION:
            self._invoke_plugin_action(nid_i)
            return
        if str(action) != "default":
            return
        handle = getattr(self, "_open_messages", {}).get(nid_i)
        callback = getattr(self, "_on_open_message", None)
        token = getattr(self, "_activation_tokens", {}).pop(nid_i, "")
        if handle and callback is not None:
            callback(handle, token)
            return
        # A click rule fires once per popup: the tracker is consumed here,
        # so a click that _open_app_target throttles leaves this popup
        # permanently unclickable rather than re-arming it.
        app_id = getattr(self, "_open_apps", {}).pop(nid_i, None)
        if app_id:
            self._open_app_target(app_id, token)

    def _open_app_target(self, app_id: str, token: str) -> None:
        """Open the rule configured for this app; never the notification text."""
        target = self._resolve_open_target(app_id)
        callback = getattr(self, "_on_open_target", None)
        if target is None or callback is None:
            return
        # A misbehaving notification server must not turn repeated action
        # signals into a stream of application launches.
        now = time.monotonic()
        if now - getattr(self, "_last_open_target", float("-inf")) < _OPEN_TARGET_INTERVAL_S:
            log.info("ignoring a repeated notification click")
            return
        self._last_open_target = now
        callback(target, token)

    def _on_closed(self, nid, reason) -> None:
        try:
            nid_i = int(nid)
            reason_i = int(reason)
        except (TypeError, ValueError):
            return

        getattr(self, "_open_messages", {}).pop(nid_i, None)
        getattr(self, "_open_apps", {}).pop(nid_i, None)
        getattr(self, "_activation_tokens", {}).pop(nid_i, None)
        call_id = getattr(self, "_call_notifications", {}).pop(nid_i, None)
        if call_id is not None:
            getattr(self, "_call_popups", {}).pop(call_id, None)
        # Closing an ANCS popup, for any reason, never runs an iPhone action.
        getattr(self, "_ancs_actions", {}).pop(nid_i, None)
        # Nor does closing a plugin popup run the plugin's action.
        getattr(self, "_plugin_popups", {}).pop(nid_i, None)
        message_path = self._pending.pop(nid_i, None)

        # Always remove the per-message subscription, no matter the reason
        sub = self._msg_subs.pop(nid_i, None)
        if sub is not None:
            try:
                sub.remove()
            except Exception:
                log.debug("could not remove message read-state watch", exc_info=True)

        if message_path is None:
            return

        # Only propagate read-state to iPhone when the human actively
        # dismissed (reason=2). Don't loop on programmatic close (reason=3,
        # which is fired when we closed it ourselves because iPhone already
        # marked it read).
        if reason_i != _REASON_DISMISSED:
            return
        if not config.MARK_READ_ON_DISMISS:
            return
        try:
            self._defer_mark_read(message_path)
        except Exception as error:
            log.debug("could not defer mark-read for %s: %s", message_path, error)
