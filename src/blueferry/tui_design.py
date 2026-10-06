"""Shared pieces of the terminal client's design, matching the Qt client.

Section names are the same as in the Qt phone card and tabs, filters are
segmented like Qt's FilterBar ("All | Missed") and cycle with ``f`` in every
list, and each dialog ends with one dimmed key-hint line in the same order:
Enter, then letters, then ``r`` refresh and ``Esc`` close.
"""
from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

# The names Qt uses for the same sections.
QUICK_SETTINGS = "Quick Settings"
TOOLS = "Tools"
FROM_PLUGINS = "From Plugins"
RECENT_CALLS = "Recent Calls"
NOTIFICATIONS = "Notifications"
NOW_PLAYING = "Now Playing"
RECENT_PHOTOS = "Recent Photos"
CONVERSATIONS = "Conversations"

# Accent colours of the terminal theme (tui.tcss uses the same values).
ACCENT = "#7dd3fc"
MISSED = "#fda4af"

THREAD_FILTERS = (("all", "All"), ("unread", "Unread"), ("starred", "Starred"))
CALL_FILTERS = (("all", "All"), ("missed", "Missed"))
PHOTO_FILTERS = (("all", "All"), ("photos", "Photos"), ("videos", "Videos"))


def next_filter(options: Sequence[tuple[str, str]], current: str) -> str:
    keys = [key for key, _label in options]
    index = keys.index(current) if current in keys else -1
    return keys[(index + 1) % len(keys)]


def filter_bar(options: Sequence[tuple[str, str]], current: str) -> Text:
    """``All | Missed`` with the current segment highlighted."""
    text = Text()
    for index, (key, label) in enumerate(options):
        if index:
            text.append("|", style="dim")
        text.append(f" {label} ", style=f"bold reverse {ACCENT}" if key == current else "dim")
    return text


def section(title: str, options: Sequence[tuple[str, str]] = (), current: str = "") -> Text:
    """A section heading, with its filter on the right like in Qt."""
    text = Text(title, style=f"bold {ACCENT}")
    if options:
        text.append("   ")
        text.append_text(filter_bar(options, current))
    return text


def key_hints(*pairs: tuple[str, str]) -> str:
    """``Enter open · f filter · r refresh · Esc close``."""
    return " · ".join(f"{key} {label}" for key, label in pairs)


def empty(text: str) -> Text:
    return Text(text, style="dim italic")
