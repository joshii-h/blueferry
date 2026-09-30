"""Keep the Bluetooth adapter identity required by iOS MAP/PBAP."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import GLib

from blueferry import bluez_setup
from blueferry.errors import PairingError

log = logging.getLogger(__name__)

RECONCILE_SECONDS = 60
# Right after bluetoothd (re)starts, the adapter can be missing from D-Bus or
# the kernel can answer the class change with MGMT status 0x0a (Busy). Retry
# such startup/restart checks quickly (2, 4, 8, 16, 32 s) instead of leaving
# iOS to refuse MAP/PBAP until the next periodic check. The budget is armed
# only by start() and poke(), so a persistently failing helper, which may
# involve a Polkit prompt, still runs at most once per RECONCILE_SECONDS.
RETRY_BASE_SECONDS = 2
RETRY_ATTEMPTS = 5

ReadClass = Callable[[str], int | None]
Matches = Callable[[int | None], bool]
Repair = Callable[[str], bool]
Schedule = Callable[[int, Callable[[], bool]], int]
Cancel = Callable[[int], object]


def _repair_with_packaged_helper(adapter: str) -> bool:
    return bluez_setup.set_cod(adapter=adapter, authorize=True)


class AdapterClassSupervisor:
    """Repair Class-of-Device drift through the constrained system helper.

    ``btmgmt class`` is volatile across controller and bluetoothd resets. A
    stale generic-computer class leaves an existing LE/ANCS bond usable while
    iOS refuses the Classic MAP/PBAP accessory path, so startup-only pairing
    configuration is not sufficient.
    """

    def __init__(
        self,
        adapter: str,
        *,
        read_class: ReadClass = bluez_setup.current_cod,
        matches: Matches = bluez_setup.desired_cod_matches,
        repair: Repair = _repair_with_packaged_helper,
        schedule: Schedule = GLib.timeout_add_seconds,
        cancel: Cancel = GLib.source_remove,
    ) -> None:
        self.adapter = adapter
        self._read_class = read_class
        self._matches = matches
        self._repair = repair
        self._schedule = schedule
        self._cancel = cancel
        self._running = False
        self._timer_id: int | None = None
        self._retry_id: int | None = None
        self._retries_left = 0
        self._retry_attempt = 0

    def start(self) -> None:
        if self._running:
            self.poke()
            return
        self._running = True
        self._timer_id = self._schedule(RECONCILE_SECONDS, self._tick)
        self._arm_retries()
        self._check()

    def poke(self) -> None:
        """Recheck immediately, notably after bluetoothd changes owner."""
        if self._running:
            self._cancel_retry()
            self._arm_retries()
            self._check()

    def stop(self) -> None:
        self._running = False
        self._cancel_retry()
        self._retries_left = 0
        if self._timer_id is None:
            return
        try:
            self._cancel(self._timer_id)
        except Exception:
            log.debug("could not remove adapter-class health timer", exc_info=True)
        self._timer_id = None

    def _tick(self) -> bool:
        if not self._running:
            return False
        self._check()
        return True

    def _arm_retries(self) -> None:
        self._retries_left = RETRY_ATTEMPTS
        self._retry_attempt = 0

    def _check(self) -> None:
        if self._reconcile():
            self._retries_left = 0
            self._cancel_retry()
        else:
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._running or self._retry_id is not None or self._retries_left <= 0:
            return
        delay = RETRY_BASE_SECONDS * (2 ** self._retry_attempt)
        self._retries_left -= 1
        self._retry_attempt += 1
        log.info("retrying adapter Class-of-Device check in %ds", delay)
        self._retry_id = self._schedule(delay, self._retry)

    def _retry(self) -> bool:
        self._retry_id = None
        if self._running:
            self._check()
        return False

    def _cancel_retry(self) -> None:
        if self._retry_id is None:
            return
        try:
            self._cancel(self._retry_id)
        except Exception:
            log.debug("could not remove adapter-class retry timer", exc_info=True)
        self._retry_id = None

    def _reconcile(self) -> bool:
        """Return False when a quick retry may succeed."""
        try:
            cod = self._read_class(self.adapter)
        except Exception:
            log.debug("could not inspect adapter Class-of-Device", exc_info=True)
            return False
        if cod is None:
            log.debug("adapter Class-of-Device is temporarily unavailable")
            return False
        if self._matches(cod):
            return True
        log.warning(
            "adapter Class-of-Device drifted to 0x%06x; restoring A/V Hands-Free",
            cod,
        )
        try:
            repaired = self._repair(self.adapter)
        except PairingError as error:
            # Missing Polkit agent or helper unit: retrying cannot help.
            log.warning("could not restore adapter Class-of-Device: %s", error)
            return True
        except Exception:
            log.warning("could not restore adapter Class-of-Device", exc_info=True)
            return False
        if repaired:
            log.info("adapter Class-of-Device restored through packaged helper")
            return True
        log.warning("packaged adapter-class helper did not repair the adapter")
        return False
