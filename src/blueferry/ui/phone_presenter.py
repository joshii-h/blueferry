"""Pure presentation rules for the GTK iPhone overview dialog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from blueferry import phone_overview as overview
from blueferry.i18n import _
from blueferry.models import BackendStatus, phone_status_fields
from blueferry.tether_status import TetherStatus


@dataclass(frozen=True)
class SwitchState:
    active: bool
    sensitive: bool
    subtitle: str


@dataclass(frozen=True)
class MediaState:
    title: str
    subtitle: str
    playing: bool
    buttons: Mapping[str, bool]


def phone_rows(status: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Battery/signal/network rows, or one row explaining why they are missing."""
    fields = phone_status_fields(BackendStatus.from_dict(status))
    return fields or [(_("Battery and Signal"), overview.phone_status_hint(status))]


def switches(
    status: Mapping[str, Any], tether: TetherStatus | None,
) -> dict[str, SwitchState]:
    audio = overview.phone_audio(status)
    hotspot = overview.tether(tether)
    lock = overview.proximity(status)
    return {
        "audio": SwitchState(audio["on_pc"], audio["available"], audio["hint"]),
        "tether": SwitchState(hotspot["active"], hotspot["available"], hotspot["hint"]),
        "lock": SwitchState(lock["enabled"], lock["available"], lock["hint"]),
    }


def media(snapshot: Mapping[str, Any] | None) -> MediaState:
    view = overview.now_playing(snapshot)
    if not view["available"]:
        title = _("Now Playing")
        subtitle = view["hint"]
    else:
        title = view["title"]
        subtitle = _("Playing") if view["playing"] else _("Paused")
    return MediaState(
        title=title,
        subtitle=subtitle,
        playing=view["playing"],
        buttons={name: name in view["commands"] for name in overview.MEDIA_BUTTONS},
    )
