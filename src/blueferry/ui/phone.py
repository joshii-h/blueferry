"""iPhone overview dialog: status, now playing, quick switches and recent calls.

Every row sets ``use_markup=False``: titles and subtitles carry iPhone text
(track titles, caller names) verbatim and must never be parsed as Pango
markup. Opt-in features that are off stay visible but insensitive, with the
opt-in hint as subtitle.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

from blueferry import phone_overview as overview
from blueferry.i18n import _
from blueferry.models import BackendStatus, CallHistoryEntry
from blueferry.tether_status import TetherStatus
from blueferry.ui.phone_presenter import media, phone_rows, switches

_MAX_CALLS = 50


def _media_icons() -> dict[str, tuple[str, str]]:
    return {
        "previous": ("media-skip-backward-symbolic", _("Previous Track")),
        "toggle": ("media-playback-start-symbolic", _("Play or Pause")),
        "next": ("media-skip-forward-symbolic", _("Next Track")),
    }


def _row(title: str = "", subtitle: str = "") -> Adw.ActionRow:
    return Adw.ActionRow(title=title, subtitle=subtitle, subtitle_lines=0, use_markup=False)


class PhonePage(Gtk.Box):
    def __init__(self, client, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._client = client
        self._toast = toast
        self._status: dict = BackendStatus().to_dict()
        self._tether: TetherStatus | None = None
        self._now_playing: dict = {}
        self._applying = False

        page = Adw.PreferencesPage()
        self.append(page)

        self._bond_banner = Adw.Banner(use_markup=False)
        self.prepend(self._bond_banner)

        self._status_group = Adw.PreferencesGroup(title=_("iPhone"))
        page.add(self._status_group)
        self._status_rows: list[Adw.ActionRow] = []

        media_group = Adw.PreferencesGroup(title=_("Now Playing"))
        page.add(media_group)
        self._media_row = _row(_("Now Playing"))
        self._media_buttons: dict[str, Gtk.Button] = {}
        for command, (icon, label) in _media_icons().items():
            button = Gtk.Button(
                icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=label,
                css_classes=["flat"],
            )
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda _b, name=command: self._media_command(name))
            self._media_row.add_suffix(button)
            self._media_buttons[command] = button
        media_group.add(self._media_row)

        switch_group = Adw.PreferencesGroup(title=_("Quick Settings"))
        page.add(switch_group)
        self._switches = {
            "audio": Adw.SwitchRow(title=_("iPhone Sound on This Computer")),
            "tether": Adw.SwitchRow(title=_("Personal Hotspot")),
            "lock": Adw.SwitchRow(title=_("Lock When the iPhone Goes Away")),
        }
        for key, row in self._switches.items():
            row.set_use_markup(False)
            row.set_subtitle_lines(0)
            row.connect("notify::active", lambda r, _p, name=key: self._switch_toggled(name, r))
            switch_group.add(row)

        self._calls_group = Adw.PreferencesGroup(title=_("Recent Calls"))
        page.add(self._calls_group)
        self._dial_row = Adw.EntryRow(title=_("Number to Dial"), show_apply_button=True)
        self._dial_row.connect("apply", lambda row: self._dial(row.get_text()))
        self._calls_group.add(self._dial_row)
        self._call_rows: list[Adw.ActionRow] = []

        client.connect("status-invalidated", lambda _c: self.refresh())
        client.connect("phone-changed", lambda _c: self._refresh_phone())
        self._render()

    # ---- loading --------------------------------------------------------

    def refresh(self) -> None:
        self._client.get_status_async(self._status_loaded)
        self._refresh_phone()

    def _refresh_phone(self) -> None:
        self._client.now_playing_async(self._media_loaded, lambda _m: self._media_loaded({}))
        self._client.tether_state_async(self._tether_loaded)
        if self._status.get("call_history_enabled") is True:
            self._client.call_history_async(self._calls_loaded, limit=_MAX_CALLS)

    def _status_loaded(self, status: BackendStatus) -> None:
        was_enabled = self._status.get("call_history_enabled") is True
        self._status = status.to_dict()
        self._render()
        if not was_enabled and self._status.get("call_history_enabled") is True:
            self._client.call_history_async(self._calls_loaded, limit=_MAX_CALLS)

    def _media_loaded(self, snapshot: dict) -> None:
        self._now_playing = snapshot if isinstance(snapshot, dict) else {}
        self._render_media()

    def _tether_loaded(self, value: TetherStatus | None) -> None:
        self._tether = value
        self._render_switches()

    def _calls_loaded(self, entries: list[CallHistoryEntry]) -> None:
        for row in self._call_rows:
            self._calls_group.remove(row)
        self._call_rows = []
        for item in overview.call_rows(entries[:_MAX_CALLS]):
            row = _row(item["caller"], f"{item['direction']} · {item['time']}")
            if item["missed"]:
                row.add_css_class("error")
            number = item["number"]
            if number:
                button = Gtk.Button(
                    icon_name="call-start-symbolic", valign=Gtk.Align.CENTER,
                    tooltip_text=_("Call"), css_classes=["flat"],
                )
                button.update_property([Gtk.AccessibleProperty.LABEL], [_("Call")])
                button.connect("clicked", lambda _b, value=number: self._dial(value))
                row.add_suffix(button)
            self._calls_group.add(row)
            self._call_rows.append(row)

    # ---- rendering ------------------------------------------------------

    def _render(self) -> None:
        bond = overview.le_bond_hint(self._status)
        self._bond_banner.set_title(bond)
        self._bond_banner.set_revealed(bool(bond))
        for row in self._status_rows:
            self._status_group.remove(row)
        self._status_rows = []
        for label, value in phone_rows(self._status):
            row = _row(label, value)
            self._status_group.add(row)
            self._status_rows.append(row)
        self._render_media()
        self._render_switches()
        history_hint = overview.call_history_hint(self._status)
        self._calls_group.set_description(history_hint or None)
        calls = self._status.get("calls_enabled") is True
        self._dial_row.set_sensitive(calls)
        self._dial_row.set_tooltip_text(
            None if calls else overview.opt_in("BLUEFERRY_CALLS_ENABLED")
        )
        if history_hint:
            self._calls_loaded([])

    def _render_media(self) -> None:
        state = media(self._now_playing)
        self._media_row.set_title(state.title)
        self._media_row.set_subtitle(state.subtitle)
        for name, button in self._media_buttons.items():
            button.set_sensitive(state.buttons[name])
        self._media_buttons["toggle"].set_icon_name(
            "media-playback-pause-symbolic" if state.playing else "media-playback-start-symbolic"
        )

    def _render_switches(self) -> None:
        self._applying = True
        try:
            for key, state in switches(self._status, self._tether).items():
                row = self._switches[key]
                row.set_active(state.active)
                row.set_sensitive(state.sensitive)
                row.set_subtitle(state.subtitle)
        finally:
            self._applying = False

    # ---- actions --------------------------------------------------------

    def _failed(self, message: str) -> None:
        self._toast(message)
        self.refresh()

    def _media_command(self, command: str) -> None:
        self._client.send_media_command_async(command, lambda _v: None, self._failed)

    def _switch_toggled(self, name: str, row: Adw.SwitchRow) -> None:
        if self._applying:
            return
        enabled = row.get_active()
        if switches(self._status, self._tether)[name].active == enabled:
            return  # a re-render, not a user change
        row.set_sensitive(False)
        if name == "audio":
            self._client.set_phone_audio_route_async(
                "pc" if enabled else "phone", lambda _v: self.refresh(), self._failed,
            )
        elif name == "tether":
            self._client.set_tether_async(enabled, self._tether_loaded, self._failed)
        else:
            grace = overview.proximity(self._status)["grace"]
            self._client.set_proximity_lock_async(
                enabled, int(grace), lambda _v: self.refresh(), self._failed,
            )

    def _dial(self, number: str) -> None:
        number = number.strip()
        if not number:
            return
        self._client.dial_async(
            number, lambda _v: self._toast(_("Calling…")), self._toast,
        )
