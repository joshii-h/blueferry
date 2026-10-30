"""Declining a call with a message (GTK): the menu on a ringing call and the
saved replies in Settings > Calls & Audio.

The ringing call gets a menu button whose popover lists the saved replies
(``Calls1.DeclineWithMessage`` with that text) and a field for an own text.
The settings group edits the saved replies (``Calls1.SetCallQuickReplies``).
Choices, validation and wording come from :mod:`blueferry.quick_reply_view`,
which the Qt and terminal clients share; the daemon validates again.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from gi.repository import Adw, Gtk

from blueferry import quick_reply_view
from blueferry.i18n import _


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


class QuickRepliesEditor:
    """Settings > Calls & Audio: the saved replies, each editable and removable.

    Shown only while the daemon reports ``calls_quick_replies``. The rows are
    rebuilt only when the saved list changes, so a status refresh keeps what
    is being typed. ``on_saved`` reads the status again after a save.
    """

    def __init__(
        self, client, toast: Callable[[str], None], *, on_saved: Callable[[], None],
    ) -> None:
        self._client = client
        self._toast = toast
        self._on_saved = on_saved
        self._reachable = False
        self._status: Mapping[str, Any] = {}
        self.saving = False
        self.replies: list[str] = []
        self.rows: list[Adw.EntryRow] = []
        self.group = Adw.PreferencesGroup(
            title=quick_reply_view.settings_label(),
            description=quick_reply_view.settings_hint(),
            visible=False,
        )
        view = quick_reply_view.settings_view({})
        self.empty_row = Adw.ActionRow(title=view["empty"], use_markup=False)
        self.empty_row.add_css_class("dim-label")
        self.group.add(self.empty_row)
        self.add_row = Adw.EntryRow(title=view["add_label"], use_markup=False)
        self.add_button = Gtk.Button(
            icon_name="list-add-symbolic", tooltip_text=view["add_label"],
            valign=Gtk.Align.CENTER, css_classes=["flat"],
        )
        self.add_button.update_property([Gtk.AccessibleProperty.LABEL], [view["add_label"]])
        self.add_button.connect("clicked", lambda _button: self.add())
        self.add_row.add_suffix(self.add_button)
        self.add_row.connect("entry-activated", lambda _row: self.add())
        self.add_row.connect("changed", lambda _row: self._render_add())
        self.group.add(self.add_row)
        self._render_add()

    def apply(self, status: Mapping[str, Any], reachable: bool) -> None:
        self._status = status
        self._reachable = reachable
        view = quick_reply_view.settings_view(status)
        self.group.set_visible(view["supported"])
        if not self.saving and view["replies"] != self.replies:
            self._show(view["replies"])
        self._render_sensitivity()

    # ---- actions ----------------------------------------------------------

    def add(self) -> None:
        text = self.add_row.get_text()
        if self.saving or not self._reachable or not text.strip():
            return
        error = quick_reply_view.reply_error(text, self.replies)
        if error:
            self._toast(error)
            return
        self._save(quick_reply_view.with_added(self.replies, text), clear_add=True)

    def edit(self, index: int) -> None:
        if self.saving or not self._reachable or not 0 <= index < len(self.rows):
            return
        text = self.rows[index].get_text()
        error = quick_reply_view.reply_error(text, self.replies, editing=index)
        if error:
            self._toast(error)
            return
        if text.strip() == self.replies[index]:
            return
        self._save(quick_reply_view.with_replaced(self.replies, index, text))

    def remove(self, index: int) -> None:
        if self.saving or not self._reachable or not 0 <= index < len(self.replies):
            return
        self._save(quick_reply_view.with_removed(self.replies, index))

    def _save(self, replies: list[str], *, clear_add: bool = False) -> None:
        self.saving = True
        self._render_sensitivity()

        def saved(result: object) -> None:
            self.saving = False
            if clear_add:
                self.add_row.set_text("")
            current = (
                quick_reply_view.replies(result) if isinstance(result, Mapping)
                and quick_reply_view.supported(result) else replies
            )
            self._show(current)
            self._render_sensitivity()
            self._on_saved()

        def failed(error: str) -> None:
            self.saving = False
            self._toast(_("Could not save the replies: {error}").format(error=error))
            # Back to what is saved.
            self._show(quick_reply_view.replies(self._status))
            self._render_sensitivity()

        self._client.set_call_quick_replies_async(replies, saved, failed)

    # ---- rendering --------------------------------------------------------

    def _show(self, replies: list[str]) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        self.replies = list(replies)
        self.group.remove(self.add_row)
        for index, text in enumerate(self.replies):
            row = Adw.EntryRow(use_markup=False, show_apply_button=True)
            row.set_text(text)
            row.update_property([Gtk.AccessibleProperty.LABEL], [text])
            row.connect("apply", lambda _row, index=index: self.edit(index))
            row.connect("changed", lambda _row, index=index: self._render_edit(index))
            remove = Gtk.Button(
                icon_name="edit-delete-symbolic", tooltip_text=_("Remove"),
                valign=Gtk.Align.CENTER, css_classes=["flat"],
            )
            remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove")])
            remove.connect("clicked", lambda _button, index=index: self.remove(index))
            row.add_suffix(remove)
            self.group.add(row)
            self.rows.append(row)
        self.group.add(self.add_row)  # always last
        self.empty_row.set_visible(not self.replies)
        self._render_add()

    def _render_edit(self, index: int) -> None:
        if 0 <= index < len(self.rows):
            row = self.rows[index]
            _mark(row, quick_reply_view.reply_error(row.get_text(), self.replies, editing=index))

    def _render_add(self) -> None:
        text = self.add_row.get_text()
        error = quick_reply_view.reply_error(text, self.replies) if text else ""
        _mark(self.add_row, error)
        self.add_row.set_visible(quick_reply_view.can_add(self.replies))
        self.add_button.set_sensitive(
            self._reachable and not self.saving and bool(text.strip()) and not error
        )

    def _render_sensitivity(self) -> None:
        editable = self._reachable and not self.saving
        for row in self.rows:
            row.set_sensitive(editable)
        self.add_row.set_sensitive(editable)
        self._render_add()


def _mark(row: Adw.EntryRow, error: str) -> None:
    """Show a validation error on an entry row (red, with the reason as tooltip)."""
    if error:
        row.add_css_class("error")
    else:
        row.remove_css_class("error")
    row.set_tooltip_text(error or None)
