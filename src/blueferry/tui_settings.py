"""Terminal settings: the same switches and plugin management as the Qt page.

``SettingsScreen`` lists notification policy, the local.env switches
(Messages1.GetFeatures/SetFeature), storage, diagnostics and the service;
``PluginsScreen`` lists installed plugins and the plugin store, installs
from a URL or the store after a confirmation, updates, removes, enables and
configures plugins from their ``[Config …]`` schema. Every blocking step
(D-Bus, git, pip, the index, plugin calls) runs on a worker thread; all
text is plain.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from blueferry import plugin_settings_view as view
from blueferry.client import BackendError
from blueferry.doctor_report import DoctorReport, run_doctor
from blueferry.features import FEATURES
from blueferry.plugin_api.client import PluginClient, PluginError
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.plugin_index import (
    DEFAULT_INDEX_URL,
    IndexFetchError,
    PluginIndex,
    check_index_url,
    index_urls,
)
from blueferry.plugin_manager import InstallError, PluginManager, PreparedInstall
from blueferry.text_safety import terminal_text

FEATURE_LABELS = {
    "show_notification_content": "Show message content in popups",
    "ancs_actions": "iPhone action buttons on popups",
    "mark_read_on_dismiss": "Mark read when a popup is dismissed",
    "notification_history": "Keep recent iPhone notifications",
    "otp_autocopy": "Copy one-time codes",
    "calls_enabled": "Phone calls on this computer (experimental)",
    "call_history_enabled": "Recent calls",
    "missed_call_notifications": "Missed-call popups",
    "keep_phone_audio_on_phone": "Keep iPhone sound on the iPhone",
    "phone_battery_notify": "Warn when the iPhone's battery is low",
    "contact_photos": "Contact photos",
    "media_control_enabled": "Now playing and media buttons",
    "media_mpris_enabled": "Desktop media controls (MPRIS)",
    "tether_autoconnect": "Connect the hotspot automatically",
}
POLICIES = ("messages", "all", "none")
POLICY_TEXT = {"all": "All iPhone notifications", "messages": "Messages only", "none": "None"}
STORAGE = ("encrypted", "plaintext", "none")
STORAGE_TEXT = {"encrypted": "Encrypted with the desktop keyring",
                "plaintext": "Unencrypted local data", "none": "Do not retain local data"}


def _plain(value: object) -> str:
    return terminal_text(value).replace("\n", " ")


def _confirmed(action: Callable[[], object]) -> Callable[[bool | None], None]:
    """A ConfirmScreen callback that runs ``action`` only after "yes"."""
    def answered(ok: bool | None) -> None:
        if ok:
            action()
    return answered


def feature_line(name: str, info: dict | None, variable: str) -> str:
    if not info:
        return f"[ ] {FEATURE_LABELS[name]}  (set {variable} in local.env)"
    mark = "x" if info.get("value") else " "
    note = ""
    if info.get("source") == "environment":
        note = "  (set by the service environment)"
    elif info.get("restart_required"):
        note = "  (after restart)"
    return f"[{mark}] {FEATURE_LABELS[name]}{note}"


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no with plain-text rows; used before installs and destructive steps."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, rows: list[tuple[str, str]], action: str) -> None:
        super().__init__()
        self._title, self._rows, self._action = title, rows, action

    def compose(self) -> ComposeResult:
        text = Text()
        for label, value in self._rows:
            text.append(f"{label}: ", style="bold")
            text.append(f"{terminal_text(value)}\n")
        with Vertical(id="confirm-settings-dialog", classes="dialog"):
            yield Static(self._title, classes="dialog-title")
            yield Static(text, id="confirm-rows", classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="confirm-no")
                yield Button(self._action, variant="primary", id="confirm-yes")

    @on(Button.Pressed, "#confirm-yes")
    def yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextPromptScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, placeholder: str, value: str = "") -> None:
        super().__init__()
        self._title, self._placeholder, self._value = title, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self._title, classes="dialog-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="prompt-cancel")
                yield Button("OK", variant="primary", id="prompt-ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#prompt-ok")
    def ok(self) -> None:
        self.dismiss(self.query_one("#prompt-input", Input).value.strip())

    @on(Button.Pressed, "#prompt-cancel")
    def cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,comma", "close", "Close", show=False),
        Binding("d", "doctor", "Diagnostics", show=False),
        Binding("p", "plugins", "Plugins", show=False),
        Binding("R", "restart", "Restart service", show=False),
    ]

    def __init__(
        self,
        client: Any,
        status: Callable[[], Any],
        *,
        doctor: Callable[[], DoctorReport] = run_doctor,
        restart: Callable[[], None] | None = None,
        plugins: Callable[[], ModalScreen] | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._status = status
        self._doctor = doctor
        self._restart = restart
        self._plugins = plugins
        self._features: dict | None = None
        self._report: DoctorReport | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog", classes="dialog"):
            yield Static("Settings", classes="dialog-title")
            with VerticalScroll(id="settings-body"):
                yield OptionList(id="settings-options")
                yield Static("", id="settings-notice", classes="dialog-copy")
                yield Static("Diagnostics", classes="field-label")
                yield Static("Press d to run blueferry doctor.", id="settings-doctor",
                             classes="dialog-copy")
            yield Static("Enter toggle · d diagnostics · p plugins · R restart service · Esc close",
                         classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Plugins…", id="settings-plugins")
                yield Button("Close", id="settings-close")

    def on_mount(self) -> None:
        self.render_options()
        self.load()

    # ---- rows ------------------------------------------------------------------

    def _rows(self) -> list[tuple[str, str]]:
        status = self._status().to_dict()
        items = (self._features or {})
        rows = [
            ("policy", "Popups: " + POLICY_TEXT.get(
                str(status.get("notification_policy") or "messages"), "Messages only")),
            ("contacts_only", f"[{'x' if status.get('contacts_only_notifications') else ' '}] "
                              "Only notify for contacts"),
        ]
        rows += [
            (f"feature:{feature.name}",
             feature_line(feature.name, items.get(feature.name), feature.variable))
            for feature in FEATURES
        ]
        policy = str(status.get("storage_policy") or "encrypted")
        rows += [
            ("storage", "Storage: " + STORAGE_TEXT.get(policy, policy)
             + f"  ({status.get('storage_state') or 'unknown'})"),
            ("unlock", "Unlock local data"),
            ("clear", "Clear history…"),
        ]
        return rows

    def render_options(self) -> None:
        options = self.query_one("#settings-options", OptionList)
        highlighted = options.highlighted
        options.clear_options()
        options.add_options([Option(_plain(text), id=key) for key, text in self._rows()])
        if highlighted is not None:
            options.highlighted = min(highlighted, options.option_count - 1)
        pending = any(
            isinstance(info, dict) and info.get("restart_required")
            for info in (self._features or {}).values()
        )
        self.query_one("#settings-notice", Static).update(
            "Some changes apply after the service restarts (press R)." if pending
            else ("Switches need a newer BlueFerry service; edit local.env instead."
                  if self._features == {} else "")
        )

    @work(thread=True, exclusive=True, group="settings-load", exit_on_error=False)
    def load(self) -> None:
        try:
            features = self._client.features()
        except (BackendError, AttributeError):
            features = {}
        self.app.call_from_thread(self._loaded, features)

    def _loaded(self, features: dict) -> None:
        self._features = features
        if self.is_attached:
            self.render_options()

    # ---- actions -------------------------------------------------------------------

    @on(OptionList.OptionSelected, "#settings-options")
    def selected(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        status = self._status().to_dict()
        if key.startswith("feature:"):
            name = key.split(":", 1)[1]
            info = (self._features or {}).get(name)
            if not info:
                self.notify("This service cannot change it; edit local.env.", severity="warning")
                return
            self._call(lambda: self._client.set_feature(name, not info.get("value")))
        elif key == "policy":
            current = str(status.get("notification_policy") or "messages")
            following = POLICIES[(POLICIES.index(current) + 1) % 3] if current in POLICIES \
                else "messages"
            self._call(lambda: self._client.set_notification_policy(following))
        elif key == "contacts_only":
            enabled = not status.get("contacts_only_notifications")
            self._call(lambda: self._client.set_contacts_only_notifications(enabled))
        elif key == "storage":
            current = str(status.get("storage_policy") or "encrypted")
            following = STORAGE[(STORAGE.index(current) + 1) % 3] if current in STORAGE \
                else "encrypted"
            self.app.push_screen(
                ConfirmScreen("Change storage?", [("New setting", STORAGE_TEXT[following])],
                              "Change"),
                _confirmed(lambda: self._call(lambda: self._client.set_storage_policy(following))),
            )
        elif key == "unlock":
            self._call(self._client.unlock_storage)
        elif key == "clear":
            self.app.push_screen(
                ConfirmScreen("Clear history?", [("Deletes", "all local BlueFerry history")],
                              "Clear"),
                _confirmed(lambda: self._call(self._client.clear_history)),
            )

    @work(thread=True, group="settings-action", exit_on_error=False)
    def _call(self, operation: Callable[[], object]) -> None:
        try:
            result = operation()
        except BackendError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        if result == "restart-required":
            self.app.call_from_thread(self.notify, "Saved; restart the service to apply it.")
        refresh = getattr(self.app, "action_refresh", None)
        if refresh is not None:
            self.app.call_from_thread(refresh)
        self.app.call_from_thread(self.load)

    def action_doctor(self) -> None:
        self.query_one("#settings-doctor", Static).update("Running blueferry doctor…")
        self._run_doctor()

    @work(thread=True, exclusive=True, group="settings-doctor", exit_on_error=False)
    def _run_doctor(self) -> None:
        report = self._doctor()
        self.app.call_from_thread(self._doctor_done, report)

    def _doctor_done(self, report: DoctorReport) -> None:
        self._report = report
        summary = ("One or more checks failed." if not report.ok
                   else "Checks completed with warnings." if report.warnings
                   else "All checks passed.")
        if self.is_attached:
            self.query_one("#settings-doctor", Static).update(
                Text(f"{summary}\n\n{terminal_text(report.text)}"))

    def action_restart(self) -> None:
        if self._restart is None:
            return
        self._restart_service()

    @work(thread=True, group="settings-action", exit_on_error=False)
    def _restart_service(self) -> None:
        try:
            if self._restart is not None:
                self._restart()
        except Exception as error:  # CommandError and friends; shown, not raised
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        self.app.call_from_thread(self.notify, "Restarted the BlueFerry service.")
        self.app.call_from_thread(self.load)

    @on(Button.Pressed, "#settings-plugins")
    def action_plugins(self) -> None:
        self.app.push_screen(self._plugins() if self._plugins else PluginsScreen())

    @on(Button.Pressed, "#settings-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class PluginConfigScreen(ModalScreen[None]):
    """A form from a plugin's settings schema; secrets are typed, never shown."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, manifest: PluginManifest, client: PluginClient) -> None:
        super().__init__()
        self._manifest = manifest
        self._client = client
        self._rows: list[dict] = view.form_fields(manifest, {})

    def compose(self) -> ComposeResult:
        with Vertical(id="plugin-config-dialog", classes="dialog"):
            yield Static(f"{_plain(self._manifest.name)} settings", classes="dialog-title")
            with VerticalScroll(id="plugin-config-body"):
                for row in self._rows:
                    yield Static(_plain(row["label"]) + (" *" if row["required"] else ""),
                                 classes="field-label")
                    widget_id = f"config-{row['key']}"
                    if row["type"] == "bool":
                        yield Checkbox(id=widget_id)
                    elif row["type"] == "choice":
                        yield Select([(choice, choice) for choice in row["choices"]],
                                     id=widget_id, allow_blank=False)
                    else:
                        yield Input(id=widget_id, password=row["type"] == "secret")
                    yield Static("", id=f"error-{row['key']}", classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="config-cancel")
                yield Button("Save", variant="primary", id="config-save")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True, exclusive=True, group="plugin-config", exit_on_error=False)
    def _load(self) -> None:
        try:
            values = self._client.get_config()
        except PluginError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        self.app.call_from_thread(self._fill, values)

    def _fill(self, values: dict) -> None:
        self._rows = view.form_fields(self._manifest, values)
        for row in self._rows:
            widget = self.query_one(f"#config-{row['key']}")
            if isinstance(widget, Checkbox):
                widget.value = row["value"] is True
            elif isinstance(widget, Select):
                if row["value"] in row["choices"]:
                    widget.value = row["value"]
            elif isinstance(widget, Input):
                if row["type"] == "secret":
                    widget.placeholder = ("stored; leave empty to keep" if row["stored"]
                                          else "not set")
                else:
                    widget.value = "" if row["value"] is None else str(row["value"])

    def _form(self) -> dict[str, object]:
        raw: dict[str, object] = {}
        for row in self._rows:
            widget = self.query_one(f"#config-{row['key']}")
            if isinstance(widget, (Checkbox, Select, Input)):
                raw[row["key"]] = widget.value
        return raw

    @on(Button.Pressed, "#config-save")
    def save(self) -> None:
        values, errors = view.parse_form(self._manifest, self._form())
        if errors:
            self._show(errors)
            return
        self._save(values)

    @work(thread=True, exclusive=True, group="plugin-config", exit_on_error=False)
    def _save(self, values: dict[str, object]) -> None:
        try:
            result = self._client.set_config(values)
        except PluginError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        if result.ok:
            self.app.call_from_thread(self.notify, "Saved.")
            self.app.call_from_thread(self.dismiss, None)
        else:
            self.app.call_from_thread(self._show, result.errors)

    def _show(self, errors: dict[str, str]) -> None:
        for row in self._rows:
            self.query_one(f"#error-{row['key']}", Static).update(
                _plain(errors.get(row["key"], "")))
        if "" in errors:
            self.notify(_plain(errors[""]), severity="error", markup=False)

    @on(Button.Pressed, "#config-cancel")
    def cancel_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class PluginsScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("i", "install_url", "Install from URL", show=False),
        Binding("e", "toggle_enabled", "Enable/disable", show=False),
        Binding("u", "update", "Update", show=False),
        Binding("x", "remove", "Remove", show=False),
        Binding("c", "configure", "Settings", show=False),
        Binding("l", "indexes", "Plugin lists", show=False),
        Binding("r", "reload", "Refresh", show=False),
    ]

    def __init__(
        self,
        *,
        manager: PluginManager | None = None,
        index: PluginIndex | None = None,
        client: Callable[[PluginManifest], PluginClient] = PluginClient,
    ) -> None:
        super().__init__()
        self._manager = manager or PluginManager()
        self._index = index or PluginIndex()
        self._client = client
        self._entries: list = []
        self._statuses: dict = {}
        self._cards: list[dict] = []
        self._problems: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="plugins-dialog", classes="dialog"):
            yield Static("Plugins", classes="dialog-title")
            yield Static("Installed", classes="field-label")
            yield OptionList(id="plugins-installed")
            yield Static("Add plugins (Enter installs)", classes="field-label")
            yield OptionList(id="plugins-store")
            yield Static("", id="plugins-hint", classes="dialog-copy")
            yield Static("c settings · e enable/disable · u update · x remove · i install URL · "
                         "l plugin lists · r refresh · Esc close", classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="plugins-close")

    def on_mount(self) -> None:
        self.reload()

    @work(thread=True, exclusive=True, group="plugins-load", exit_on_error=False)
    def reload(self, refresh: bool = False) -> None:
        entries, _ignored = self._manager.entries()
        statuses = view.plugin_statuses(entries, self._client)
        try:
            catalog = view.load_catalog(self._manager, self._index, entries, refresh=refresh)
            cards = [view.store_row(item) for item in catalog.items]
            problems = list(catalog.problems)
        except IndexFetchError as error:
            cards, problems = [], [("index", str(error))]
        self.app.call_from_thread(self._loaded, entries, statuses, cards, problems)

    def _loaded(self, entries, statuses, cards, problems) -> None:
        self._entries, self._statuses, self._cards, self._problems = (
            entries, statuses, cards, problems)
        if not self.is_attached:
            return
        installed = self.query_one("#plugins-installed", OptionList)
        installed.clear_options()
        for entry in entries:
            row = view.entry_row(entry, statuses.get(entry.manifest.id))
            line = (f"{row['name']} {row['version']} · {row['stateText']} · "
                    f"{', '.join(row['capabilities'])} · {row['source']}")
            installed.add_option(Option(_plain(line), id=row["id"]))
        if not entries:
            installed.add_option(Option("No plugins installed.", id="", disabled=True))
        store = self.query_one("#plugins-store", OptionList)
        store.clear_options()
        for card in cards:
            line = (f"{card['emoji'] + ' ' if card['emoji'] else ''}{card['name']}  "
                    f"[{card['stateText']}]  {', '.join(card['badges'])}  {card['description']}")
            store.add_option(Option(_plain(line), id=card["id"],
                                    disabled=not card["installable"]))
        self.query_one("#plugins-hint", Static).update(
            "\n".join(_plain(f"{url}: {problem}") for url, problem in problems))

    def _selected_entry(self):
        options = self.query_one("#plugins-installed", OptionList)
        if options.highlighted is None or not self._entries:
            return None
        option_id = options.get_option_at_index(options.highlighted).id
        return next((e for e in self._entries if e.manifest.id == option_id), None)

    # ---- install ------------------------------------------------------------------

    @on(OptionList.OptionSelected, "#plugins-store")
    def store_selected(self, event: OptionList.OptionSelected) -> None:
        card = next((c for c in self._cards if c["id"] == event.option.id), None)
        if card is None or not card["installable"]:
            return
        installed = any(e.manifest.id == card["id"] and e.managed for e in self._entries)
        if card["state"] == "update" and installed:
            self._prepare(lambda: self._manager.prepare_update(card["id"]), "Update")
        else:
            self._prepare(lambda: self._manager.prepare(card["repo"], card["ref"] or None),
                          "Install")

    def action_install_url(self) -> None:
        def chosen(url: str | None) -> None:
            if url:
                self._prepare(lambda: self._manager.prepare(url), "Install")

        self.app.push_screen(TextPromptScreen(
            "Install from Git URL", "https://github.com/…/blueferry-plugin-…"), chosen)

    @work(thread=True, group="plugins-action", exit_on_error=False)
    def _prepare(self, operation: Callable[[], PreparedInstall | None], verb: str) -> None:
        self.app.call_from_thread(self.notify, "Fetching the plugin…")
        try:
            prepared = operation()
        except InstallError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        if prepared is None:
            self.app.call_from_thread(self.notify, "The plugin is up to date.")
            return
        self.app.call_from_thread(self._confirm, prepared, verb)

    def _confirm(self, prepared: PreparedInstall, verb: str) -> None:
        rows = [(row["label"], row["value"]) for row in view.summary_rows(prepared)]
        rows.append(("Note", "A plugin runs as your user. Install only plugins you trust."))

        def answered(ok: bool | None) -> None:
            if ok:
                self._commit(prepared, verb)
            else:
                self._discard(prepared)

        self.app.push_screen(ConfirmScreen(f"{verb} plugin?", rows, verb), answered)

    @work(thread=True, group="plugins-action", exit_on_error=False)
    def _discard(self, prepared: PreparedInstall) -> None:
        self._manager.discard(prepared)

    @work(thread=True, group="plugins-action", exit_on_error=False)
    def _commit(self, prepared: PreparedInstall, verb: str) -> None:
        self.app.call_from_thread(self.notify, "Creating the plugin's environment…")
        try:
            record = self._manager.commit(prepared)
        except InstallError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        done = "Updated" if verb == "Update" else "Installed"
        self.app.call_from_thread(
            self.notify, _plain(f"{done} {prepared.manifest.name} {record.ref_label}."))
        self.app.call_from_thread(self.reload)

    # ---- installed plugins ----------------------------------------------------------

    def action_toggle_enabled(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._run(lambda: self._manager.set_enabled(entry.manifest.id, not entry.enabled))

    def action_update(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.managed:
            self.notify("Only plugins installed from a URL can be updated here.",
                        severity="warning")
            return
        self._prepare(lambda: self._manager.prepare_update(entry.manifest.id), "Update")

    def action_remove(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.managed:
            self.notify("Only plugins installed from a URL can be removed here.",
                        severity="warning")
            return
        self.app.push_screen(
            ConfirmScreen(f"Remove {entry.manifest.name}?",
                          [("Keeps", "the plugin's own settings and keyring entries")], "Remove"),
            _confirmed(lambda: self._run(lambda: self._manager.remove(entry.manifest.id))),
        )

    def action_configure(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.manifest.config:
            self.notify("This plugin has no settings.", severity="warning")
            return
        self.app.push_screen(PluginConfigScreen(entry.manifest, self._client(entry.manifest)))

    def action_indexes(self) -> None:
        current = ", ".join(index_urls(self._manager.settings()))

        def chosen(value: str | None) -> None:
            if value is None:
                return
            try:
                urls = [check_index_url(part) for part in value.split(",") if part.strip()]
            except InstallError as error:
                self.notify(_plain(error), severity="error", markup=False)
                return
            self._run(lambda: self._manager.update_settings(indexes=urls), refresh=True)

        self.app.push_screen(TextPromptScreen(
            "Plugin lists (comma-separated https URLs)", DEFAULT_INDEX_URL, current), chosen)

    @work(thread=True, group="plugins-action", exit_on_error=False)
    def _run(self, operation: Callable[[], object], refresh: bool = False) -> None:
        try:
            operation()
        except (InstallError, OSError) as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        self.app.call_from_thread(self.reload, refresh)

    def action_reload(self) -> None:
        self.reload(True)

    @on(Button.Pressed, "#plugins-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
