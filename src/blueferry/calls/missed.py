"""Missed-call detection on the HFP path and its dedupe against call history.

With both optional integrations on, one unanswered call is seen twice: live
through oFono when it stops ringing, and later as a PBAP call-history entry.
The HFP path announces it at once and remembers, in memory only and only
briefly, when and from which number it did so; call history then skips its
own popup for the same call.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from blueferry.call_history import canonical_phone_digits
from blueferry.calls.model import CallEvent, CallRecord
from blueferry.events import normalize_phone

# How long an HFP-announced missed call can suppress a call-history popup.
# Call history is polled and additionally synced right after a call ends, so
# its entry normally arrives within seconds; ten minutes covers a slow pull.
MISSED_MEMORY_SECONDS = 600
# The phone's call-history timestamp and the moment oFono first reported the
# ringing call differ by at most a few seconds in practice.
MISSED_MATCH_SECONDS = 120
MAX_REMEMBERED_MISSED_CALLS = 64

# oFono states that mean the call was picked up at some point.
_ANSWERED_STATES = frozenset({"active", "held"})


def call_number_identity(number: str | None) -> str | None:
    """Fold a number like ``CallRecord.number_identity``; ``None`` if withheld."""
    if not number:
        return None
    return canonical_phone_digits(normalize_phone(number)) or number


@dataclass(frozen=True)
class MissedHfpCall:
    record: CallRecord
    declined: bool
    occurred_at: datetime


class MissedCallTracker:
    """Classify HFP call events; report a ringing call that ended unanswered."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._ringing: dict[str, datetime] = {}
        self._answered: set[str] = set()
        self._declined: set[str] = set()

    def declined(self, call_id: str) -> None:
        """The user declined this call from a BlueFerry notification."""
        if call_id in self._ringing:
            self._declined.add(call_id)

    def observe(self, event: CallEvent) -> MissedHfpCall | None:
        record = event.call
        call_id = record.call_id
        if event.kind != "call_ended":
            if record.ringing and call_id not in self._ringing:
                self._ringing[call_id] = self._now()
            if record.state in _ANSWERED_STATES:
                self._answered.add(call_id)
            return None
        started = self._ringing.pop(call_id, None)
        answered = call_id in self._answered
        declined = call_id in self._declined
        self._answered.discard(call_id)
        self._declined.discard(call_id)
        if started is None or answered:
            return None
        return MissedHfpCall(record=record, declined=declined, occurred_at=started)


class MissedCallMemory:
    """Short-lived (time, number identity) pairs of HFP-announced missed calls.

    Never persisted and never logged; entries expire after
    ``MISSED_MEMORY_SECONDS`` and each suppresses at most one history notice.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: deque[tuple[float, datetime, str | None]] = deque(
            maxlen=MAX_REMEMBERED_MISSED_CALLS,
        )

    def remember(self, occurred_at: datetime, identity: str | None) -> None:
        self._prune()
        self._entries.append((self._clock(), _aware(occurred_at), identity or None))

    def suppresses(self, occurred_at: datetime, identity: str | None) -> bool:
        """True (and forget the match) if HFP already announced this call."""
        self._prune()
        when = _aware(occurred_at)
        wanted = identity or None
        for entry in self._entries:
            _seen, announced_at, announced_identity = entry
            if announced_identity != wanted:
                continue
            if abs((announced_at - when).total_seconds()) <= MISSED_MATCH_SECONDS:
                self._entries.remove(entry)
                return True
        return False

    def __len__(self) -> int:
        self._prune()
        return len(self._entries)

    def _prune(self) -> None:
        cutoff = self._clock() - MISSED_MEMORY_SECONDS
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.astimezone()
