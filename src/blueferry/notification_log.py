"""Opt-in, memory-only log of recent iPhone app notifications.

The private history deliberately retains only Apple Messages ANCS records
(``history.minimize_ancs_history`` deletes everything else at every start),
so there is no stored app-notification history to read. This log keeps the
most recent non-Messages ANCS notifications of the running daemon in memory
only, for the clients' notification list. It is off unless
``BLUEFERRY_NOTIFICATION_HISTORY`` is set, stores titles and bodies only when
``BLUEFERRY_SHOW_NOTIFICATION_CONTENT`` is true, and is never written to disk.

With ``mirror_iphone_removals`` (default on) the list follows the iPhone: an
ANCS NotificationRemoved drops the record, and after a reconnect the records
of the previous ANCS session that the iPhone does not report again as
PreExisting within a short settle window are dropped too. ANCS UIDs are only
unique within one session, so a removal only ever matches a record of the
current session; old records are adopted into the new session when the
iPhone reports their UID as PreExisting. Matching across a reconnect relies
on iOS keeping a notification's UID while it exists. With the setting off,
the list is a plain recent history.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

from blueferry.ancs.constants import MESSAGES_APP_ID

DEFAULT_CAPACITY = 200
MAX_FIELD_CHARS = 512
# How long after a new ANCS session is authorized the iPhone gets to report
# its existing notifications before older records are reconciled.
RECONCILE_SETTLE_SECONDS = 10
_SESSION = "_session"


def _bounded(value: object) -> str:
    text = str(value or "")
    return text if len(text) <= MAX_FIELD_CHARS else text[: MAX_FIELD_CHARS - 1] + "…"


class NotificationLog:
    """Bounded ring of content-gated ANCS records; also an event sink."""

    name = "notification-log"
    # Ask the dispatcher for non-Messages ANCS events as well.
    accepts_system_ancs = True

    def __init__(
        self,
        *,
        show_content: bool,
        on_changed: Callable[[], None] | None = None,
        capacity: int = DEFAULT_CAPACITY,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        idle: Callable[[Callable[[], bool]], object] | None = None,
        mirror_removals: Callable[[], bool] = lambda: False,
        schedule: Callable[[int, Callable[[], bool]], object] | None = None,
        cancel: Callable[[object], object] | None = None,
    ) -> None:
        self._show_content = show_content
        self._on_changed = on_changed
        self._clock = clock
        # With an idle scheduler (GLib.idle_add in the daemon), a burst of
        # notifications yields one NotificationsChanged per loop iteration.
        self._idle = idle
        self._emit_pending = False
        self._records: deque[dict[str, object]] = deque(maxlen=max(1, capacity))
        self._mirror_removals = mirror_removals
        self._schedule = schedule
        self._cancel = cancel
        # ANCS session counter; records carry the session they belong to.
        self._session = 0
        self._reconcile_pending = False
        self._reconcile_id: object | None = None

    def set_listener(self, on_changed: Callable[[], None]) -> None:
        self._on_changed = on_changed

    def handle(self, _event) -> None:
        """MAP events are not app notifications."""

    def handle_ancs(self, event) -> None:
        app_id = str(getattr(event, "app_id", "") or "")
        if not app_id or app_id == MESSAGES_APP_ID:
            return
        seen = getattr(event, "seen_at", None)
        when = seen if isinstance(seen, datetime) else self._clock()
        record: dict[str, object] = {
            _SESSION: self._session,
            "id": int(getattr(event, "notification_id", 0) or 0),
            "app_id": _bounded(app_id),
            "app_name": _bounded(getattr(event, "app_name", "")),
            "time": when.isoformat(),
        }
        if self._show_content:
            for key in ("title", "subtitle", "body"):
                record[key] = _bounded(getattr(event, key, ""))
        # ANCS reports a modified notification again with the same uid.
        for index, existing in enumerate(self._records):
            if existing["id"] == record["id"] and existing["app_id"] == record["app_id"]:
                self._records[index] = record
                break
        else:
            self._records.append(record)
        self._notify()

    # ---- following removals on the iPhone ---------------------------------

    def set_mirror_removals(self, mirror_removals: Callable[[], bool]) -> None:
        self._mirror_removals = mirror_removals

    def remove(self, notification_id: int) -> None:
        """ANCS NotificationRemoved: drop this session's record for the UID."""
        if not self._mirror_removals():
            return
        uid = int(notification_id)
        kept = [
            record for record in self._records
            if not (record["id"] == uid and record[_SESSION] == self._session)
        ]
        if len(kept) != len(self._records):
            self._replace(kept)

    def session_reset(self) -> None:
        """The ANCS session ended: its UIDs mean nothing to the next one."""
        self._session += 1
        self._cancel_reconcile()
        self._reconcile_pending = any(
            record[_SESSION] != self._session for record in self._records
        )

    def preexisting(self, notification_id: int) -> None:
        """The iPhone still has this notification: keep the older record."""
        if not self._reconcile_pending:
            return
        uid = int(notification_id)
        for record in self._records:
            if record["id"] == uid and record[_SESSION] != self._session:
                record[_SESSION] = self._session

    def session_ready(self) -> None:
        """Notification access is back; reconcile after the settle window."""
        if not self._reconcile_pending or self._reconcile_id is not None:
            return
        if self._schedule is None:
            self.reconcile()
            return

        def fire() -> bool:
            self._reconcile_id = None
            self.reconcile()
            return False

        self._reconcile_id = self._schedule(RECONCILE_SETTLE_SECONDS, fire)

    def reconcile(self) -> None:
        """Drop older records the iPhone did not report again."""
        self._cancel_reconcile()
        pending, self._reconcile_pending = self._reconcile_pending, False
        if not pending or not self._mirror_removals():
            return
        kept = [record for record in self._records if record[_SESSION] == self._session]
        if len(kept) != len(self._records):
            self._replace(kept)

    def close(self) -> None:
        self._cancel_reconcile()

    def _cancel_reconcile(self) -> None:
        source, self._reconcile_id = self._reconcile_id, None
        if source is not None and self._cancel is not None:
            self._cancel(source)

    def _replace(self, records: list[dict[str, object]]) -> None:
        self._records = deque(records, maxlen=self._records.maxlen)
        self._notify()

    def clear(self) -> None:
        had = bool(self._records)
        self._records.clear()
        if had:
            self._notify()

    def _notify(self) -> None:
        if self._on_changed is None:
            return
        if self._idle is None:
            self._on_changed()
            return
        if self._emit_pending:
            return
        self._emit_pending = True

        def fire() -> bool:
            self._emit_pending = False
            if self._on_changed is not None:
                self._on_changed()
            return False

        self._idle(fire)

    def snapshot(self, limit: int) -> list[dict[str, object]]:
        """Newest first, at most ``limit`` records (copies)."""
        bounded = max(0, int(limit))
        return [
            {key: value for key, value in record.items() if key != _SESSION}
            for record in list(reversed(self._records))[:bounded]
        ]
