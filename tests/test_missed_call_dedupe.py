"""One missed call, one popup: HFP and PBAP call history deduplicated.

Hermetic: call events are fed straight into the daemon, and every sink,
controller operation, and history pull is replaced by a recorder.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from blueferry import config
from blueferry.call_history import MISSED
from blueferry.call_history import CallRecord as HistoryRecord
from blueferry.calls.missed import (
    MISSED_MATCH_SECONDS,
    MISSED_MEMORY_SECONDS,
    MissedCallMemory,
    MissedCallTracker,
    call_number_identity,
)
from blueferry.calls.model import CallEvent, CallRecord

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def _call(state="incoming", *, call_id="voicecall01", number="+41791234567", **extra):
    return CallRecord(
        call_id=call_id,
        path=f"/hfp/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/{call_id}",
        state=state,
        direction="incoming",
        number=number,
        **extra,
    )


def _event(kind, state="incoming", **values):
    return CallEvent(kind, _call(state, **values))


def _history(address="+41791234567", *, at=NOW, phone=None):
    digits = phone if phone is not None else "".join(c for c in address if c.isdigit())
    return HistoryRecord(
        direction=MISSED, occurred_at=at, raw_time=at.strftime("%Y%m%dT%H%M%SZ"),
        address=address, phone=digits, name=None,
    )


# ---- tracker -------------------------------------------------------------

def test_ringing_call_that_ends_unanswered_is_missed() -> None:
    tracker = MissedCallTracker(now=lambda: NOW)

    assert tracker.observe(_event("call_incoming")) is None
    missed = tracker.observe(_event("call_ended"))

    assert missed is not None
    assert missed.occurred_at == NOW and missed.declined is False


def test_answered_or_outgoing_calls_are_not_missed() -> None:
    tracker = MissedCallTracker(now=lambda: NOW)
    tracker.observe(_event("call_incoming"))
    tracker.observe(_event("call_changed", "active"))
    assert tracker.observe(_event("call_ended", "disconnected")) is None

    tracker.observe(_event("call_changed", "dialing", call_id="voicecall02"))
    assert tracker.observe(_event("call_ended", call_id="voicecall02")) is None


def test_decline_from_the_notification_is_reported_as_declined() -> None:
    tracker = MissedCallTracker(now=lambda: NOW)
    tracker.observe(_event("call_incoming"))
    tracker.declined("voicecall01")

    missed = tracker.observe(_event("call_ended"))

    assert missed is not None and missed.declined is True


# ---- memory --------------------------------------------------------------

def test_number_identity_matches_call_history_spellings() -> None:
    assert call_number_identity("+41 79 123 45 67") == _history().number_identity
    assert call_number_identity("0041791234567") == _history().number_identity
    assert call_number_identity("") is None


@pytest.mark.parametrize(
    "offset,identity,expected",
    [
        (0, "41791234567", True),
        (MISSED_MATCH_SECONDS, "41791234567", True),
        (-MISSED_MATCH_SECONDS, "41791234567", True),
        (MISSED_MATCH_SECONDS + 1, "41791234567", False),
        (0, "41790000000", False),
        (0, None, False),
    ],
)
def test_memory_matches_number_and_time_window(offset, identity, expected) -> None:
    memory = MissedCallMemory(clock=lambda: 0.0)
    memory.remember(NOW, "41791234567")

    assert memory.suppresses(NOW + timedelta(seconds=offset), identity) is expected


def test_withheld_numbers_match_each_other() -> None:
    memory = MissedCallMemory(clock=lambda: 0.0)
    memory.remember(NOW, None)

    assert memory.suppresses(NOW, "") is True


def test_each_announcement_suppresses_one_history_entry_and_expires() -> None:
    now = [0.0]
    memory = MissedCallMemory(clock=lambda: now[0])
    memory.remember(NOW, "41791234567")
    assert memory.suppresses(NOW, "41791234567") is True
    assert memory.suppresses(NOW, "41791234567") is False

    memory.remember(NOW, "41791234567")
    now[0] += MISSED_MEMORY_SECONDS + 1
    assert len(memory) == 0
    assert memory.suppresses(NOW, "41791234567") is False


def test_naive_history_times_are_read_in_local_time() -> None:
    memory = MissedCallMemory(clock=lambda: 0.0)
    memory.remember(NOW, "41791234567")

    local = NOW.astimezone().replace(tzinfo=None)
    assert memory.suppresses(local, "41791234567") is True


# ---- daemon wiring -------------------------------------------------------

@pytest.fixture
def daemon(make_daemon, monkeypatch):
    monkeypatch.setattr(config, "MISSED_CALL_NOTIFICATIONS", True)
    instance = make_daemon()
    instance._missed_tracker = MissedCallTracker(now=lambda: NOW)
    popups: list = []
    syncs: list[str] = []
    monkeypatch.setattr(instance.events, "call", lambda _event: None)
    monkeypatch.setattr(instance.events, "missed_calls", popups.append)
    monkeypatch.setattr(instance, "request_call_history_sync", syncs.append)
    monkeypatch.setattr(instance.contacts, "resolve", lambda _raw: None)
    return instance, popups, syncs


def test_hfp_missed_call_pops_up_once_and_history_stays_silent(daemon) -> None:
    instance, popups, syncs = daemon

    instance._on_call_event(_event("call_incoming", contact_name="Alice"))
    instance._on_call_event(_event("call_ended", contact_name="Alice"))

    assert [(n.caller, n.known_contact, n.occurred_at) for n in popups[0]] == [
        ("Alice", True, NOW),
    ]
    assert syncs == ["call ended"]

    # The phone's call list arrives a little later with its own timestamp.
    instance._missed_calls([_history("0041791234567", at=NOW - timedelta(seconds=20))])
    assert len(popups) == 1

    # A different missed call in the same pull is still announced.
    instance._missed_calls([_history("+41790000000")])
    assert [n.caller for n in popups[1]] == ["+41790000000"]


def test_declined_call_is_neither_announced_nor_repeated_by_history(
    daemon, monkeypatch,
) -> None:
    instance, popups, syncs = daemon
    monkeypatch.setattr(instance.calls, "hangup", lambda *_args: None)

    instance._on_call_event(_event("call_incoming"))
    instance._notification_call_action("voicecall01", "decline")
    instance._on_call_event(_event("call_ended"))
    instance._missed_calls([_history()])

    assert popups == []
    assert syncs == ["call ended"]


def test_answered_call_only_refreshes_history(daemon) -> None:
    instance, popups, syncs = daemon

    instance._on_call_event(_event("call_incoming"))
    instance._on_call_event(_event("call_changed", "active"))
    instance._on_call_event(_event("call_ended", "disconnected"))

    assert popups == []
    assert syncs == ["call ended"]


def test_missed_call_sub_flag_silences_hfp_but_still_dedupes(daemon, monkeypatch) -> None:
    instance, popups, _syncs = daemon
    monkeypatch.setattr(config, "MISSED_CALL_NOTIFICATIONS", False)

    instance._on_call_event(_event("call_incoming"))
    instance._on_call_event(_event("call_ended"))

    assert popups == []
    assert len(instance._hfp_missed_calls) == 1
