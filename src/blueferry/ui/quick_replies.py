"""Declining a call with a message (GTK): the menu on a ringing call.

The ringing call gets a menu button whose popover lists the saved replies
(``Calls1.DeclineWithMessage`` with that text) and a field for an own text.
Choices, validation and wording come from :mod:`blueferry.quick_reply_view`,
which the Qt and terminal clients share; the daemon validates again.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from gi.repository import Adw, Gtk

from blueferry import quick_reply_view


class DeclineMessageMenu:
    """A menu button: pick a saved reply or type one, then ``send(text)``.

    ``draft`` puts back an own text typed before the row was rebuilt;
    ``on_draft`` follows every change of it.
    """

    def __init__(
        self,
        choices: Sequence[Mapping[str, str]],
        send: Callable[[str], None],
        *,
        draft: str = "",
        on_draft: Callable[[str], None] = lambda _text: None,
    ) -> None:
        self._send = send
        self._on_draft = on_draft
        label = quick_reply_view.button_label()
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=320,
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6,
        )
        content.append(Gtk.Label(
            label=quick_reply_view.dialog_title(), use_markup=False, xalign=0,
            css_classes=["heading"],
        ))
        self.list = Gtk.ListBox(
            css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
        )
        self.reply_rows: list[Adw.ActionRow] = []
        for choice in choices:
            value = str(choice.get("value") or "")
            if value == quick_reply_view.CUSTOM or not value:
                continue
            row = Adw.ActionRow(
                title=str(choice.get("label") or value), use_markup=False, activatable=True,
            )
            row.connect("activated", lambda _row, text=value: self._choose(text))
            self.list.append(row)
            self.reply_rows.append(row)
        self.custom = Adw.EntryRow(title=quick_reply_view.custom_placeholder(), use_markup=False)
        self.custom.set_text(draft)
        self.custom.connect("changed", self._custom_changed)
        self.custom.connect("entry-activated", lambda _row: self.send_custom())
        self.send_button = Gtk.Button(
            icon_name="mail-send-symbolic", tooltip_text=label, valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        self.send_button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.send_button.connect("clicked", lambda _button: self.send_custom())
        self.custom.add_suffix(self.send_button)
        self.list.append(self.custom)
        content.append(self.list)
        self.error = Gtk.Label(
            use_markup=False, wrap=True, xalign=0, visible=False, css_classes=["error"],
        )
        content.append(self.error)
        self.popover = Gtk.Popover(child=content)
        self.button = Gtk.MenuButton(
            icon_name="mail-reply-sender-symbolic", tooltip_text=label,
            valign=Gtk.Align.CENTER, css_classes=["circular", "flat"], popover=self.popover,
        )
        self.button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._custom_changed(self.custom)

    def send_custom(self) -> None:
        text = self.custom.get_text()
        error = quick_reply_view.custom_text_error(text)
        self.error.set_label(error)
        self.error.set_visible(bool(error))
        if not error:
            self._choose(text.strip())

    def _choose(self, text: str) -> None:
        self.popover.popdown()
        self._send(text)

    def _custom_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text()
        self.send_button.set_sensitive(bool(text.strip()))
        if text.strip():
            self.error.set_visible(False)
        self._on_draft(text)
