"""Adapter Class-of-Device supervision without Bluetooth or systemd."""

from blueferry import adapter_class_supervisor
from blueferry.adapter_class_supervisor import AdapterClassSupervisor


def _supervisor(calls, state, scheduled):
    return AdapterClassSupervisor(
        "hci7",
        read_class=lambda adapter: (
            calls.append(("read", adapter)),
            state["class"],
        )[-1],
        matches=lambda value: value == 0x408,
        repair=lambda adapter: (
            calls.append(("repair", adapter)),
            state.__setitem__("class", 0x408),
            True,
        )[-1],
        schedule=lambda delay, callback: scheduled.append((delay, callback)) or 7,
        cancel=lambda timer_id: calls.append(("cancel", timer_id)),
    )


def test_start_repairs_a_drifted_adapter_class() -> None:
    calls = []
    state = {"class": 0x104}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)

    supervisor.start()

    assert calls == [("read", "hci7"), ("repair", "hci7")]
    assert state["class"] == 0x408
    assert scheduled[0][0] == adapter_class_supervisor.RECONCILE_SECONDS


def test_matching_adapter_class_is_left_alone() -> None:
    calls = []
    state = {"class": 0x408}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)

    supervisor.start()

    assert calls == [("read", "hci7")]


def test_periodic_reconciliation_repairs_later_drift() -> None:
    calls = []
    state = {"class": 0x408}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)
    supervisor.start()

    state["class"] = 0x104
    assert scheduled[0][1]() is True

    assert calls[-2:] == [("read", "hci7"), ("repair", "hci7")]


def test_unknown_adapter_state_waits_without_invoking_helper() -> None:
    calls = []
    state = {"class": None}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)

    supervisor.start()

    assert calls == [("read", "hci7")]


def test_read_failure_does_not_stop_periodic_reconciliation() -> None:
    scheduled = []

    def fail(_adapter):
        raise RuntimeError("gone")

    supervisor = AdapterClassSupervisor(
        "hci7",
        read_class=fail,
        schedule=lambda delay, callback: scheduled.append((delay, callback)) or 7,
        cancel=lambda _timer: None,
    )

    supervisor.start()

    assert scheduled[0][1]() is True


def test_bluez_restart_poke_rechecks_immediately() -> None:
    calls = []
    state = {"class": 0x408}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)
    supervisor.start()
    state["class"] = 0x104

    supervisor.poke()

    assert calls[-2:] == [("read", "hci7"), ("repair", "hci7")]


def test_stop_cancels_reconciliation() -> None:
    calls = []
    state = {"class": 0x408}
    scheduled = []
    supervisor = _supervisor(calls, state, scheduled)
    supervisor.start()

    supervisor.stop()

    assert calls[-1] == ("cancel", 7)
    assert scheduled[0][1]() is False


class _BusyHelper:
    """The packaged helper failing like `btmgmt class` with status 0x0a."""

    def __init__(self, state, failures):
        self.state = state
        self.failures = failures
        self.calls = 0

    def __call__(self, _adapter):
        self.calls += 1
        if self.failures:
            self.failures -= 1
            return False
        self.state["class"] = 0x408
        return True


def _retrying_supervisor(state, repair, scheduled, cancelled):
    return AdapterClassSupervisor(
        "hci7",
        read_class=lambda _adapter: state["class"],
        matches=lambda value: value == 0x408,
        repair=repair,
        schedule=lambda delay, callback: scheduled.append((delay, callback)) or len(scheduled),
        cancel=cancelled.append,
    )


def test_busy_repair_after_bluez_restart_is_retried_with_backoff() -> None:
    state = {"class": 0x408}
    scheduled, cancelled = [], []
    helper = _BusyHelper(state, failures=2)
    supervisor = _retrying_supervisor(state, helper, scheduled, cancelled)
    supervisor.start()
    state["class"] = 0x104  # bluetoothd restarted with a generic class

    supervisor.poke()
    assert helper.calls == 1
    assert [delay for delay, _ in scheduled[1:]] == [2]

    assert scheduled[1][1]() is False
    assert [delay for delay, _ in scheduled[1:]] == [2, 4]
    assert scheduled[2][1]() is False

    assert helper.calls == 3
    assert state["class"] == 0x408
    assert len(scheduled) == 3


def test_retries_stop_after_the_budget_and_fall_back_to_the_periodic_check() -> None:
    state = {"class": 0x104}
    scheduled, cancelled = [], []
    helper = _BusyHelper(state, failures=100)
    supervisor = _retrying_supervisor(state, helper, scheduled, cancelled)

    supervisor.start()
    fired = 1  # the periodic timer
    while fired < len(scheduled):
        assert scheduled[fired][1]() is False
        fired += 1
        assert fired < 20

    delays = [delay for delay, _ in scheduled[1:]]
    assert delays == [2, 4, 8, 16, 32]
    assert helper.calls == 1 + adapter_class_supervisor.RETRY_ATTEMPTS

    # The periodic check keeps trying but does not re-arm quick retries.
    assert scheduled[0][1]() is True
    assert len(scheduled) == 1 + adapter_class_supervisor.RETRY_ATTEMPTS


def test_missing_polkit_or_helper_is_not_retried_quickly() -> None:
    from blueferry.errors import PairingError

    state = {"class": 0x104}
    scheduled, cancelled = [], []

    def refuse(_adapter):
        raise PairingError("no Polkit agent")

    supervisor = _retrying_supervisor(state, refuse, scheduled, cancelled)
    supervisor.start()

    assert [delay for delay, _ in scheduled] == [adapter_class_supervisor.RECONCILE_SECONDS]


def test_missing_adapter_after_restart_is_retried_and_stop_cancels_it() -> None:
    state = {"class": None}
    scheduled, cancelled = [], []
    supervisor = _retrying_supervisor(state, _BusyHelper(state, 0), scheduled, cancelled)

    supervisor.start()
    assert [delay for delay, _ in scheduled] == [
        adapter_class_supervisor.RECONCILE_SECONDS,
        adapter_class_supervisor.RETRY_BASE_SECONDS,
    ]

    supervisor.stop()
    assert cancelled == [2, 1]
    assert scheduled[1][1]() is False
    assert len(scheduled) == 2
