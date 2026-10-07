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

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Collapsible, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from blueferry import companion_tools, plugin_logs
from blueferry import plugin_settings_view as view
from blueferry import tui_design as design
from blueferry.client import BackendError
from blueferry.doctor_report import DoctorReport, run_doctor
from blueferry.features import FEATURES
from blueferry.plugin_api.client import PluginClient, PluginError
from blueferry.plugin_api.config_flow import LOGIN_POLL_SECONDS, LOGIN_TIMEOUT_SECONDS, LoginStep
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.plugin_index import (
    DEFAULT_INDEX_URL,
    IndexFetchError,
    PluginIndex,
    check_index_url,
    index_urls,
)
from blueferry.plugin_manager import InstallError, PluginManager, PreparedInstall
from blueferry.plugin_stopper import StopOutcome, stop_note, stop_ok
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


class LogScreen(ModalScreen[None]):
    """The last lines of a plugin's log file, as plain text."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, title: str, text: str, path: str) -> None:
        super().__init__()
        self._title, self._text, self._path = title, text, path

    def compose(self) -> ComposeResult:
        with Vertical(id="plugin-log-dialog", classes="dialog"):
            yield Static(self._title, classes="dialog-title", markup=False)
            with VerticalScroll(id="plugin-log-body"):
                yield Static(Text(terminal_text(self._text)), id="plugin-log-text")
            yield Static(Text(terminal_text(self._path), style="dim"), classes="dialog-copy")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="plugin-log-close")

    def on_mount(self) -> None:
        self.query_one("#plugin-log-body", VerticalScroll).scroll_end(animate=False)

    @on(Button.Pressed, "#plugin-log-close")
    def action_close(self) -> None:
        self.dismiss(None)


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
                         classes="key-hints")
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
    """A form from a plugin's settings schema; secrets are typed, never shown.

    Same form as the Qt client (ApiVersion 1.3): sections, "Advanced"
    folded, placeholders, examples, a help link per field, inline errors
    from the pre-check, Test connection, the browser sign-in and one status
    line. Save stays disabled until the visible required fields are valid.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close", show=False)]

    def __init__(
        self,
        manifest: PluginManifest,
        client: PluginClient,
        *,
        opener: Callable[[str], object] | None = None,
        poll_seconds: float = LOGIN_POLL_SECONDS,
    ) -> None:
        super().__init__()
        self._manifest = manifest
        self._client = client
        self._opener = opener
        self._poll_seconds = poll_seconds
        self._rows: list[dict] = view.form_fields(manifest, {})
        self._stored: dict[str, object] = {}
        self._touched: set[str] = set()
        self._filling = False
        self._valid = False
        self._login_stop = threading.Event()
        self._actions = view.form_actions(manifest)

    # ---- layout --------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="plugin-config-dialog", classes="dialog"):
            yield Static(f"{_plain(self._manifest.name)} settings", classes="dialog-title")
            with VerticalScroll(id="plugin-config-body"):
                for group in view.form_groups(self._manifest):
                    rows = [row for row in self._rows if row["group"] == group["name"]]
                    if not rows:
                        continue
                    if group["collapsed"]:
                        with Collapsible(title=_plain(group["label"]), collapsed=True,
                                         id=f"group-{group['name']}"):
                            yield from self._group_help(group)
                            for row in rows:
                                yield from self._field(row)
                        continue
                    if group["label"]:
                        yield Static(design.section(_plain(group["label"])),
                                     classes="section-title")
                    yield from self._group_help(group)
                    for row in rows:
                        yield from self._field(row)
            yield Static("", id="config-status", classes="config-status")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="config-cancel")
                if self._actions["test"]:
                    yield Button("Test connection", id="config-test")
                if self._actions["loginLabel"]:
                    yield Button(self._actions["loginLabel"], id="config-login")
                    yield Button("Cancel sign-in", id="config-login-cancel", classes="hidden")
                yield Button("Save", variant="primary", id="config-save", disabled=True)

    def _group_help(self, group: dict) -> ComposeResult:
        if group["help"]:
            yield Static(_plain(group["help"]), classes="config-help")

    def _field(self, row: dict) -> ComposeResult:
        key = row["key"]
        with Vertical(id=f"row-{key}", classes="config-row"):
            yield Static(_plain(row["label"]) + (" *" if row["required"] else ""),
                         classes="field-label")
            widget_id = f"config-{key}"
            if row["type"] == "bool":
                yield Checkbox(id=widget_id)
            elif row["type"] == "choice":
                yield Select([(choice, choice) for choice in row["choices"]],
                             id=widget_id, allow_blank=False)
            else:
                with Horizontal(classes="config-input"):
                    yield Input(id=widget_id, password=row["type"] == "secret",
                                placeholder=_plain(row["placeholder"]))
                    if row["type"] == "secret":
                        yield Button("Show", id=f"reveal-{key}", classes="config-reveal")
            yield Static("", id=f"error-{key}", classes="config-error")
            if row["help"]:
                yield Static(_plain(row["help"]), classes="config-help")
            if row["example"]:
                yield Static(_plain(f"Example: {row['example']}"), classes="config-help")
            if row["helpUrl"]:
                yield Button("Where do I find this?", id=f"help-{key}", classes="config-link")

    def on_mount(self) -> None:
        self._recheck()
        self._load()

    # ---- values --------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="plugin-config", exit_on_error=False)
    def _load(self) -> None:
        try:
            values = self._client.get_config()
        except PluginError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        self.app.call_from_thread(self._fill, values)

    def _fill(self, values: dict) -> None:
        self._stored = dict(values)
        self._rows = view.form_fields(self._manifest, values)
        self._filling = True
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
                                          else _plain(row["placeholder"]) or "not set")
                else:
                    widget.value = "" if row["value"] is None else str(row["value"])
        self._filling = False
        self._touched.clear()
        self._recheck()

    def _form(self, *, touched_only: bool = False) -> dict[str, object]:
        raw: dict[str, object] = {}
        for row in self._rows:
            if touched_only and row["key"] not in self._touched:
                continue
            widget = self.query_one(f"#config-{row['key']}")
            if isinstance(widget, (Checkbox, Select, Input)):
                raw[row["key"]] = widget.value
        return raw

    @on(Input.Changed)
    @on(Checkbox.Changed)
    @on(Select.Changed)
    def _edited(self, event: Input.Changed | Checkbox.Changed | Select.Changed) -> None:
        widget_id = event.control.id or ""
        if self._filling or not widget_id.startswith("config-"):
            return
        self._touched.add(widget_id[len("config-"):])
        self._recheck()

    def _recheck(self) -> None:
        if not self.is_attached or not self.query("#config-save"):
            return
        check = view.check_form(self._manifest, self._stored, self._form(touched_only=True))
        visible = set(check["visible"])
        for row in self._rows:
            self.query_one(f"#row-{row['key']}").display = row["key"] in visible
            self.query_one(f"#error-{row['key']}", Static).update(
                _plain(check["errors"].get(row["key"], "")))
        self._valid = bool(check["valid"])
        self.query_one("#config-save", Button).disabled = not self._valid
        if self._actions["test"]:
            self.query_one("#config-test", Button).disabled = not self._valid

    def _status(self, text: str, *, ok: bool = True) -> None:
        status = self.query_one("#config-status", Static)
        status.update(_plain(text))
        status.set_class(not ok, "config-status-error")

    def _show(self, errors: dict[str, str]) -> None:
        for row in self._rows:
            self.query_one(f"#error-{row['key']}", Static).update(
                _plain(errors.get(row["key"], "")))
        if "" in errors:
            self.notify(_plain(errors[""]), severity="error", markup=False)

    # ---- buttons -------------------------------------------------------------------

    @on(Button.Pressed, ".config-reveal")
    def _reveal(self, event: Button.Pressed) -> None:
        key = (event.button.id or "")[len("reveal-"):]
        field = self.query_one(f"#config-{key}", Input)
        field.password = not field.password
        event.button.label = "Show" if field.password else "Hide"

    @on(Button.Pressed, ".config-link")
    def _help(self, event: Button.Pressed) -> None:
        url = view.help_link(self._manifest, (event.button.id or "")[len("help-"):])
        if url:
            self._open(url)

    def _open(self, url: str) -> None:
        try:
            if self._opener is not None:
                self._opener(url)
            else:
                companion_tools.default_system().open_uri(url)
        except Exception as error:  # no browser must not end the TUI
            self.notify(f"Could not open: {_plain(error)}", severity="error", markup=False)

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

    @on(Button.Pressed, "#config-test")
    def test_connection(self) -> None:
        values, errors = view.parse_form(self._manifest, self._form())
        if errors:
            self._show(errors)
            return
        self._status("Testing the connection…")
        self._test(values)

    @work(thread=True, exclusive=True, group="plugin-config", exit_on_error=False)
    def _test(self, values: dict[str, object]) -> None:
        try:
            result = self._client.test_config(values)
        except PluginError as error:
            self.app.call_from_thread(self._status, str(error), ok=False)
            return
        self.app.call_from_thread(self._tested, result)

    def _tested(self, result: Any) -> None:
        self._status(("✓ " if result.ok else "✗ ") + result.message, ok=result.ok)
        if result.errors:
            self._show(result.errors)

    @on(Button.Pressed, "#config-login")
    def sign_in(self) -> None:
        self._login_stop.set()
        self._login_stop = threading.Event()
        self._status(view.login_text("pending"))
        self._signing_in(True)
        self._login(view.login_values(self._manifest, self._form()), self._login_stop)

    @on(Button.Pressed, "#config-login-cancel")
    def cancel_sign_in(self) -> None:
        self._login_stop.set()
        self._status(view.login_text("cancelled"), ok=False)
        self._signing_in(False)

    def _signing_in(self, running: bool) -> None:
        self.query_one("#config-login", Button).set_class(running, "hidden")
        self.query_one("#config-login-cancel", Button).set_class(not running, "hidden")

    @work(thread=True, group="plugin-login", exit_on_error=False)
    def _login(self, values: dict[str, object], stop: threading.Event) -> None:
        try:
            step = self._client.config_login(values)
            if step.state == "open":
                self.app.call_from_thread(self._open, step.open_uri)
                self.app.call_from_thread(
                    self._status, view.login_text("pending", step.message))
                login_id = step.login_id
                deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
                while not step.final:
                    if stop.wait(self._poll_seconds):
                        self._client.config_login_cancel(login_id)
                        return
                    if time.monotonic() > deadline:
                        self._client.config_login_cancel(login_id)
                        step = LoginStep("expired")
                        break
                    step = self._client.config_login_status(login_id)
        except PluginError as error:
            if not stop.is_set():
                self.app.call_from_thread(self._login_done, LoginStep("error", str(error)))
            return
        if not stop.is_set():
            self.app.call_from_thread(self._login_done, step)

    def _login_done(self, step: LoginStep) -> None:
        if not self.is_attached:
            return
        ok = step.state == "done"
        self._status(("✓ " if ok else "✗ ") + view.login_text(step.state, step.message), ok=ok)
        self._signing_in(False)
        if ok:
            self._load()

    @on(Button.Pressed, "#config-cancel")
    def cancel_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        self._login_stop.set()
        self.dismiss(None)


class PluginsScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("i", "install_url", "Install from URL", show=False),
        Binding("e", "toggle_enabled", "Enable/disable", show=False),
        Binding("u", "update", "Update", show=False),
        Binding("x", "remove", "Remove", show=False),
        Binding("c", "configure", "Settings", show=False),
        Binding("g", "log", "Log", show=False),
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
            yield Static("Installed", classes="field-label section-title")
            yield OptionList(id="plugins-installed")
            yield Static("Add Plugins (Enter installs)", classes="field-label section-title")
            yield OptionList(id="plugins-store")
            yield Static("", id="plugins-hint", classes="dialog-copy")
            yield Static("c settings · g log · e enable/disable · u update · x remove · "
                         "i install URL · l plugin lists · r refresh · Esc close",
                         classes="key-hints")
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
        note = stop_note(prepared.stop)
        self.app.call_from_thread(
            self.notify, _plain(f"{done} {prepared.manifest.name} {record.ref_label}."
                                + (f" {note}" if note else "")),
            severity="information" if stop_ok(prepared.stop) else "warning")
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

    def action_log(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._load_log(entry.manifest.id, entry.manifest.name)

    @work(thread=True, group="plugins-log", exit_on_error=False)
    def _load_log(self, plugin_id: str, name: str) -> None:
        path, text = plugin_logs.tail(plugin_id)
        self.app.call_from_thread(self._show_log, name, path, text)

    def _show_log(self, name: str, path: Path | None, text: str) -> None:
        if path is None:
            self.notify("This plugin has not written a log yet.", severity="warning")
            return
        self.app.push_screen(LogScreen(f"Log: {name}", text or "(empty)", str(path)))

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
            result = operation()
        except (InstallError, OSError) as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        # Removal carries the outcome in .stop; disabling returns it directly.
        outcome = getattr(result, "stop", result)
        if isinstance(outcome, StopOutcome) and (note := stop_note(outcome, restarts=False)):
            self.app.call_from_thread(self.notify, _plain(note), markup=False,
                                      severity="information" if stop_ok(outcome) else "warning")
        self.app.call_from_thread(self.reload, refresh)

    def action_reload(self) -> None:
        self.reload(True)

    @on(Button.Pressed, "#plugins-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
