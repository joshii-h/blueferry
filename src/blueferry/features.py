"""Switches the settings UIs may change: Messages1.GetFeatures/SetFeature.

Each feature is one boolean ``local.env`` variable from a fixed allowlist.
SetFeature stores the choice in settings.json (``"features"``), which
:mod:`blueferry.config` applies before local.env the next time the daemon
starts; an explicit process environment still wins. The daemon reads these
values once at start-up, so a change reports ``restart-required`` until the
service restarts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blueferry import config
from blueferry.settings_store import SettingsStore


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    variable: str
    default: bool


FEATURES: tuple[Feature, ...] = (
    Feature("show_notification_content", "BLUEFERRY_SHOW_NOTIFICATION_CONTENT", True),
    Feature("ancs_actions", "BLUEFERRY_ANCS_ACTIONS", False),
    Feature("mark_read_on_dismiss", "BLUEFERRY_MARK_READ_ON_DISMISS", True),
    Feature("notification_history", "BLUEFERRY_NOTIFICATION_HISTORY", False),
    Feature("otp_autocopy", "BLUEFERRY_OTP_AUTOCOPY", False),
    Feature("calls_enabled", "BLUEFERRY_CALLS_ENABLED", False),
    Feature("call_history_enabled", "BLUEFERRY_CALL_HISTORY_ENABLED", False),
    Feature("missed_call_notifications", "BLUEFERRY_MISSED_CALL_NOTIFICATIONS", True),
    Feature("keep_phone_audio_on_phone", "BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE", True),
    Feature("phone_battery_notify", "BLUEFERRY_PHONE_BATTERY_NOTIFY", False),
    Feature("contact_photos", "BLUEFERRY_CONTACT_PHOTOS", False),
    Feature("media_control_enabled", "BLUEFERRY_MEDIA_CONTROL_ENABLED", False),
    Feature("media_mpris_enabled", "BLUEFERRY_MEDIA_MPRIS_ENABLED", False),
    Feature("tether_autoconnect", "BLUEFERRY_TETHER_AUTOCONNECT", False),
)
BY_NAME: Mapping[str, Feature] = {feature.name: feature for feature in FEATURES}


def running_values() -> dict[str, bool]:
    """What this daemon process uses, as computed by config at start-up."""
    return {
        "show_notification_content": config.SHOW_NOTIFICATION_CONTENT,
        "ancs_actions": config.ANCS_ACTIONS,
        "mark_read_on_dismiss": config.MARK_READ_ON_DISMISS,
        "notification_history": config.NOTIFICATION_HISTORY,
        "otp_autocopy": config.OTP_AUTOCOPY,
        "calls_enabled": config.CALLS_ENABLED,
        "call_history_enabled": config.CALL_HISTORY_ENABLED,
        "missed_call_notifications": config.MISSED_CALL_NOTIFICATIONS,
        "keep_phone_audio_on_phone": config.KEEP_PHONE_AUDIO_ON_PHONE,
        "phone_battery_notify": config.PHONE_BATTERY_NOTIFY,
        "contact_photos": config.CONTACT_PHOTOS,
        "media_control_enabled": config.MEDIA_CONTROL_ENABLED,
        "media_mpris_enabled": config.MEDIA_MPRIS_ENABLED,
        "tether_autoconnect": config.TETHER_AUTOCONNECT,
    }


class FeatureSettings:
    """Read and store the switches; the daemon's view of them."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        running: Callable[[], Mapping[str, bool]] = running_values,
        explicit: frozenset[str] | None = None,
        local_env: Callable[[], Mapping[str, str]] = config.read_local_env,
    ) -> None:
        self._path = path or config.SETTINGS_JSON
        self._store = SettingsStore(self._path)
        self._running = running
        self._explicit = config.EXPLICIT_ENV_KEYS if explicit is None else explicit
        self._local_env = local_env

    def _stored(self) -> dict[str, bool]:
        return config.read_feature_settings(self._path)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Per feature: value (after restart), running, source, restart_required."""
        stored, running, local = self._stored(), self._running(), self._local_env()
        result: dict[str, dict[str, Any]] = {}
        for feature in FEATURES:
            now = bool(running.get(feature.name, feature.default))
            if feature.variable in self._explicit:
                source, value = "environment", now
            elif feature.variable in stored:
                source, value = "settings", stored[feature.variable]
            elif feature.variable in local:
                source, value = "local.env", now
            else:
                source, value = "default", feature.default
            result[feature.name] = {
                "value": value,
                "running": now,
                "source": source,
                "variable": feature.variable,
                "restart_required": value != now,
            }
        # MPRIS only runs together with media control.
        mpris, media = result["media_mpris_enabled"], result["media_control_enabled"]
        mpris["restart_required"] = (mpris["value"] and media["value"]) != mpris["running"]
        return result

    def set(self, name: str, enabled: bool) -> str:
        """Store one switch; ``restart-required``, ``active`` or ``environment``."""
        feature = BY_NAME.get(name)
        if feature is None:
            raise KeyError(name)
        stored = self._stored()
        stored[feature.variable] = bool(enabled)
        self._store.update(**{config.FEATURES_SETTINGS_KEY: dict(sorted(stored.items()))})
        if feature.variable in self._explicit:
            return "environment"
        running = bool(self._running().get(name, feature.default))
        return "active" if running == bool(enabled) else "restart-required"
