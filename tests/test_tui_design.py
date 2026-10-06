"""The terminal client's shared section names, filters and key hints."""
from __future__ import annotations

from blueferry import tui_design as design


def test_filters_cycle_and_show_the_current_segment() -> None:
    assert design.next_filter(design.THREAD_FILTERS, "all") == "unread"
    assert design.next_filter(design.THREAD_FILTERS, "starred") == "all"
    assert design.next_filter(design.CALL_FILTERS, "bogus") == "all"
    bar = design.filter_bar(design.CALL_FILTERS, "missed")
    assert bar.plain == " All | Missed "
    assert any("reverse" in str(span.style) and bar.plain[span.start:span.end] == " Missed "
               for span in bar.spans)


def test_sections_and_hints_use_the_qt_names() -> None:
    assert design.section(design.RECENT_CALLS, design.CALL_FILTERS, "all").plain == (
        "Recent Calls    All | Missed ")
    assert (design.QUICK_SETTINGS, design.TOOLS, design.FROM_PLUGINS) == (
        "Quick Settings", "Tools", "From Plugins")
    assert design.key_hints(("Enter", "open"), ("Esc", "close")) == "Enter open · Esc close"
