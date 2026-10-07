"""Terminal settings and plugin management with fakes; no D-Bus, git or pip."""
from __future__ import annotations

import json

from textual.widgets import Input, OptionList

from blueferry.doctor_report import DoctorReport
from blueferry.plugin_api import logs as api_logs
from blueferry.plugin_api.client import ConfigResult, PluginStatus
from blueferry.plugin_api.config import SECRET_MASK
from blueferry.plugin_index import PluginIndex
from blueferry.plugin_manager import PluginManager
from blueferry.tui import BlueFerryApp, TuiState
from blueferry.tui_settings import (
    ConfirmScreen,
    LogScreen,
    PluginConfigScreen,
    PluginsScreen,
    SettingsScreen,
)
from tests.test_plugin_manager import URL, FakeRunner, FakeStopper
from tests.test_tui_phone import _Backend, _plain, _run, _until


class _SettingsBackend(_Backend):
    def __init__(self) -> None:
        super().__init__()
        self.switches = {"calls_enabled": {"value": False, "source": "default",
                                           "restart_required": False}}

    def features(self):
        return self.switches

    def set_feature(self, name, enabled):
        self.requests.append(("feature", name, enabled))
        self.switches[name] = {"value": enabled, "source": "settings",
                               "restart_required": True}
        return "restart-required"

    def set_notification_policy(self, policy):
        self.requests.append(("policy", policy))
        return policy


def test_settings_toggle_switches_and_run_diagnostics() -> None:
    async def scenario() -> None:
        backend = _SettingsBackend()
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 44)) as pilot:
            await _until(pilot, lambda: app.state.status.calls_enabled)
            app.set_focus(None)
            await pilot.press("comma")
            await _until(pilot, lambda: isinstance(app.screen, SettingsScreen))
            screen = app.screen
            options = screen.query_one("#settings-options", OptionList)
            await _until(pilot, lambda: "Phone calls" in str(
                options.get_option("feature:calls_enabled").prompt))
            options.focus()
            options.highlighted = 0
            await pilot.press("enter")
            await _until(pilot, lambda: ("policy", "all") in backend.requests)
            index = next(i for i in range(options.option_count)
                         if options.get_option_at_index(i).id == "feature:calls_enabled")
            options.highlighted = index
            await pilot.press("enter")
            await _until(pilot, lambda: ("feature", "calls_enabled", True) in backend.requests)
            await _until(pilot, lambda: "after restart" in str(
                options.get_option("feature:calls_enabled").prompt))
            assert "restart" in _plain(app, "#settings-notice")
            screen._doctor = lambda: DoctorReport(True, True, "WARNING [b]x[/b]")
            await pilot.press("d")
            await _until(pilot, lambda: "warnings" in _plain(app, "#settings-doctor"))
            assert "[b]x[/b]" in _plain(app, "#settings-doctor")
            await pilot.press("escape")
            await _until(pilot, lambda: not isinstance(app.screen, SettingsScreen))

    _run(scenario())


INDEX = json.dumps({"version": 1, "plugins": [
    {"id": "io.example.demo", "name": "Demo photos", "description": "[b]Photos[/b]",
     "repo": URL, "ref": "v0.1.0", "capabilities": ["photos"]},
    {"id": "io.example.cal", "name": "Calendar", "repo": URL + "-cal", "ref": None,
     "capabilities": ["calendar"]},
]}).encode()


class _Client:
    saved: list = []  # noqa: RUF012 - shared by the per-plugin instances

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    def status(self):
        return PluginStatus("ok")

    def get_config(self):
        return {"url": "https://a.example", "api_key": SECRET_MASK}

    def set_config(self, values):
        _Client.saved.append(dict(values))
        return ConfigResult(True, {})


def test_plugins_install_from_the_store_after_confirmation_and_configure(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    runner = FakeRunner()
    secret = "[Config api_key]\nLabel=API key\nType=secret\n\n[Config url]"
    runner.files = {
        commit: {name: text.replace("[Config url]", secret) for name, text in files.items()}
        for commit, files in runner.files.items()
    }
    manager = PluginManager(data_home=tmp_path / "data", settings_path=tmp_path / "p.json",
                            runner=runner, python="/usr/bin/python3",
                            stopper=FakeStopper())
    index = PluginIndex(fetch=lambda _url: INDEX, cache=tmp_path / "cache")
    _Client.saved = []

    async def scenario() -> None:
        app = BlueFerryApp(TuiState(_Backend()), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 44)) as pilot:
            app.push_screen(PluginsScreen(manager=manager, index=index, client=_Client))
            await _until(pilot, lambda: isinstance(app.screen, PluginsScreen))
            screen = app.screen
            await _until(pilot, lambda: bool(screen.query("#plugins-store")))
            store = screen.query_one("#plugins-store", OptionList)
            await _until(pilot, lambda: store.option_count == 2)
            assert "[b]Photos[/b]" in str(store.get_option("io.example.demo").prompt)
            assert store.get_option("io.example.cal").disabled
            store.focus()
            store.highlighted = 0
            await pilot.press("enter")
            await _until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            assert URL in _plain(app, "#confirm-rows")
            assert not any("pip" in " ".join(call) for call in runner.calls)
            await pilot.click("#confirm-yes")
            await _until(pilot, lambda: "io.example.demo" in manager.records())
            installed = screen.query_one("#plugins-installed", OptionList)
            await _until(pilot, lambda: installed.get_option_at_index(0).id == "io.example.demo")
            installed.focus()
            installed.highlighted = 0
            await pilot.press("c")
            await _until(pilot, lambda: isinstance(app.screen, PluginConfigScreen))
            secret = app.screen.query_one("#config-api_key", Input)
            await _until(pilot, lambda: "stored" in secret.placeholder)
            assert secret.value == "" and secret.password
            secret.value = "n3w"
            await pilot.click("#config-save")
            await _until(pilot, lambda: _Client.saved)
            assert _Client.saved[0]["api_key"] == "n3w"
            await _until(pilot, lambda: isinstance(app.screen, PluginsScreen))
            await pilot.press("g")
            await _until(pilot, lambda: "This plugin has not written a log yet." in [
                note.message for note in app._notifications])
            log = api_logs.log_path("io.example.demo")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("ready\nescape \x1b[2J done\n")
            await pilot.press("g")
            await _until(pilot, lambda: isinstance(app.screen, LogScreen))
            shown = _plain(app, "#plugin-log-text")
            assert "ready" in shown and "done" in shown and "\x1b" not in shown
            await pilot.press("escape")
            await _until(pilot, lambda: isinstance(app.screen, PluginsScreen))
            manager.stopper.state = "stopped"
            await pilot.press("e")
            await _until(pilot, lambda: manager.disabled() == {"io.example.demo"})
            await _until(pilot, lambda: "Stopped the running plugin." in [
                note.message for note in app._notifications])

    _run(scenario())


GUIDED = """ConfigTest=true
ConfigLogin=nextcloud

[Config url]
Label=Server URL
Type=url
Required=true
Group=account
Placeholder=https://cloud.example.com
Example=https://cloud.example.com/nextcloud
HelpUrl=https://docs.example.com/url

[Config app_password]
Label=App password
Type=secret
Required=true
Group=account

[Config resize]
Label=Resize pictures
Type=bool
Advanced=true

[Config size]
Label=Longest side
Type=int
Min=320
Max=8000
Default=2048
Advanced=true
ShowIf=resize=true
ErrorText=Use 320 to 8000 pixels.
"""


def test_guided_plugin_form_checks_tests_and_signs_in() -> None:
    from textual.widgets import Button, Checkbox, Collapsible

    from blueferry.plugin_api.client import PluginClient
    from blueferry.plugin_api.config_flow import ConfigTestResult, LoginStep
    from blueferry.plugin_api.service import CardService
    from blueferry.plugin_api.testing import ServiceTransport, inline_service, manifest

    class Cloud(CardService):
        stored: dict = {}  # noqa: RUF012 - one instance per test
        polls = 0

        def config_values(self):
            return dict(self.stored)

        def apply_config(self, values):
            self.stored.update(values)

        def test_config(self, values):
            return ConfigTestResult(True, f"Connected to {values['url']}")

        def config_login(self, provider, values):
            return LoginStep("open", login_id="f1", open_uri="https://cloud.example/login/v2/f")

        def config_login_status(self, login_id):
            Cloud.polls += 1
            if Cloud.polls < 2:
                return LoginStep("pending")
            self.stored.update(url="https://cloud.example", app_password="pw")
            return LoginStep("done", "Connected as anna")

    plugin = manifest("io.example.cloud", capabilities="card;", api_version="1.3", extra=GUIDED)
    client = PluginClient(plugin, transport=ServiceTransport(inline_service(Cloud, plugin)))
    opened: list[str] = []

    async def scenario() -> None:
        app = BlueFerryApp(TuiState(_Backend()), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 60)) as pilot:
            app.push_screen(PluginConfigScreen(plugin, client, opener=opened.append,
                                               poll_seconds=0.01))
            await _until(pilot, lambda: isinstance(app.screen, PluginConfigScreen))
            screen = app.screen
            await _until(pilot, lambda: bool(screen.query("#config-url")))
            url = screen.query_one("#config-url", Input)
            assert url.placeholder == "https://cloud.example.com"
            assert "Example: https://cloud.example.com/nextcloud" in _texts(screen)
            assert screen.query_one("#group-advanced", Collapsible).collapsed
            save = screen.query_one("#config-save", Button)
            await _until(pilot, lambda: save.disabled)
            assert not screen.query_one("#row-size").display
            screen.query_one("#config-resize", Checkbox).value = True
            await _until(pilot, lambda: screen.query_one("#row-size").display)
            url.value = "http://cloud.example"
            await _until(pilot, lambda: "https" in _plain(app, "#error-url"))
            url.value = "https://cloud.example"
            screen.query_one("#config-app_password", Input).value = "typed"
            await _until(pilot, lambda: not save.disabled)
            screen.query_one("#config-size", Input).value = "9"
            await _until(pilot, lambda: _plain(app, "#error-size") == "Use 320 to 8000 pixels.")
            assert save.disabled
            screen.query_one("#config-size", Input).value = "1024"
            await _until(pilot, lambda: not save.disabled)
            secret = screen.query_one("#config-app_password", Input)
            assert secret.password
            screen.query_one("#reveal-app_password", Button).press()
            await _until(pilot, lambda: not secret.password)
            screen.query_one("#help-url", Button).press()
            await _until(pilot, lambda: opened == ["https://docs.example.com/url"])
            screen.query_one("#config-test", Button).press()
            await _until(pilot, lambda: "Connected to https://cloud.example"
                         in _plain(app, "#config-status"))
            assert Cloud.stored == {}
            screen.query_one("#config-login", Button).press()
            await _until(pilot, lambda: "Connected as anna" in _plain(app, "#config-status"))
            assert opened[-1] == "https://cloud.example/login/v2/f"
            await _until(pilot, lambda: "stored" in secret.placeholder)

    _run(scenario())


def _texts(screen) -> str:
    from textual.widgets import Static

    return "\n".join(str(widget.render()) for widget in screen.query(Static))
