"""Pure presentation logic for the BlueFerry system tray item.

Kept free of Qt so the badge, tooltip and menu states are unit-testable.
Everything here works on the already decoded GetStatus, ListThreads and
Tether1.GetState replies; nothing performs I/O.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from blueferry.companion_tools import ToolState
from blueferry.i18n import _, ngettext
from blueferry.models import BackendStatus, Thread, phone_status_fields
from blueferry.qt.phone_link import phone_audio
from blueferry.reconnect_view import bluez_text, reconnect_view
from blueferry.tether_status import TetherStatus

MAX_BADGE = 99


@dataclass(frozen=True, slots=True)
class TrayToggle:
    """One checkable menu entry."""

    visible: bool
    enabled: bool
    checked: bool
    text: str


def unread_total(threads: Iterable[Thread]) -> int:
    return sum(max(0, thread.unread_count) for thread in threads)


def badge_text(count: int) -> str:
    """The number painted over the phone icon; empty when nothing is unread."""
    if count <= 0:
        return ""
    return f"{MAX_BADGE}+" if count > MAX_BADGE else str(count)


def connection_line(status: BackendStatus | None, error: str = "") -> str:
    if status is None:
        return error or _("BlueFerry service is not running")
    if status.map:
        return _("iPhone connected")
    if status.initializing:
        return _("Connecting…")
    return _("iPhone offline")


def tooltip(status: BackendStatus | None, unread: int, error: str = "") -> str:
    """Plain-text tooltip: connection, battery/signal/network, unread count."""
    lines = [connection_line(status, error)]
    if status is not None and status.extra.get("bluez_unresponsive") is True:
        lines.append(bluez_text(status.extra))
    if status is not None:
        lines.extend(
            _("{label}: {value}").format(label=label, value=value)
            for label, value in phone_status_fields(status)
        )
    if unread > 0:
        lines.append(
            ngettext("{count} unread message", "{count} unread messages", unread)
            .format(count=unread)
        )
    return "\n".join(lines)


def audio_toggle(status: Mapping[str, Any] | None) -> TrayToggle:
    """"Sound on this computer": checked while the iPhone plays on the PC."""
    text = _("iPhone sound on this computer")
    if status is None:
        return TrayToggle(False, False, False, text)
    audio = phone_audio(status)
    return TrayToggle(
        visible=bool(audio["supported"]),
        enabled=bool(audio["available"]) and not audio["pending"],
        checked=bool(audio["onPc"]),
        text=text,
    )


def hotspot_toggle(tether: TetherStatus | None) -> TrayToggle:
    """The iPhone Personal Hotspot over Bluetooth; hidden without Tether1."""
    text = _("Personal Hotspot")
    if tether is None:
        return TrayToggle(False, False, False, text)
    return TrayToggle(
        visible=True,
        enabled=tether.settled,
        checked=tether.active,
        text=text,
    )


def mirror_toggle(status: Mapping[str, Any] | None) -> TrayToggle:
    """"Sync notifications with iPhone"; hidden for backends without it."""
    text = _("Sync notifications with iPhone")
    value = None if status is None else status.get("mirror_iphone_removals")
    if value is None:
        return TrayToggle(False, False, False, text)
    return TrayToggle(True, True, value is True, text)


@dataclass(frozen=True, slots=True)
class TrayAction:
    visible: bool
    enabled: bool
    text: str


def reconnect_entry(status: Mapping[str, Any] | None) -> TrayAction:
    """"Reconnect iPhone" while the Classic link is down and offered."""
    view = reconnect_view(status)
    return TrayAction(view.offered, view.offered, _("Reconnect iPhone"))


def audio_route_for(checked: bool) -> str:
    return "pc" if checked else "phone"


@dataclass(frozen=True, slots=True)
class TrayEntry:
    """One companion tool entry in the tray menu."""

    key: str
    visible: bool
    enabled: bool
    text: str


def tool_entries(
    tools: Iterable[ToolState], *, busy: str = "", needs_pairing: bool = False,
) -> list[TrayEntry]:
    """Mirror, LocalSend, iPhone photos, eject and (when needed) pairing.

    Missing tools stay listed but disabled, like in the phone card; eject
    only shows while the photos are mounted.
    """
    entries: list[TrayEntry] = []
    for tool in tools:
        if tool.key == "eject":
            entries.append(TrayEntry(tool.key, tool.enabled, tool.enabled and not busy, tool.title))
            continue
        text = tool.title if tool.installed else _("{tool} (not installed)").format(tool=tool.title)
        if busy == tool.key:
            text = _("{tool} …").format(tool=tool.title)
        entries.append(TrayEntry(tool.key, True, tool.enabled and not busy, text))
    entries.append(TrayEntry(
        "pair", needs_pairing, needs_pairing and not busy,
        _("Ask the iPhone to trust this computer"),
    ))
    return entries


# Theme icons of the menu entries, the same the phone card uses.
ICONS = {
    "open": "io.weirdware.BlueFerry",
    "audio": "audio-speakers-symbolic",
    "hotspot": "network-wireless-hotspot",
    "mirror_notifications": "preferences-desktop-notification",
    "reconnect": "view-refresh",
    "mirror": "video-display",
    "send": "document-send",
    "photos": "folder-pictures",
    "eject": "media-eject",
    "pair": "emblem-locked",
    "share": "document-send",
    "quit": "application-exit",
}
# Section titles; the same names as the phone card and the terminal client.
SECTION_QUICK = _("Quick Settings")
SECTION_TOOLS = _("Tools")


def share_menu_title(loading: bool, count: int) -> str:
    if loading and count == 0:
        return _("Send to… (looking for targets)")
    return _("Send to…")
