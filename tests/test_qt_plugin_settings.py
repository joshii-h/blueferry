"""Qt plugin management, feature switches and the doctor: fakes only."""
from __future__ import annotations

import json
from typing import ClassVar

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication

from blueferry.doctor_report import DoctorReport, parse_output
from blueferry.plugin_api.client import ConfigResult, PluginStatus
from blueferry.plugin_api.config import SECRET_MASK
from blueferry.plugin_index import PluginIndex
from blueferry.plugin_manager import PluginManager
from blueferry.qt.plugin_settings import PluginSettings
from tests.test_plugin_manager import URL, FakeRunner


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QGuiApplication([])


def _wait(predicate) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(10)
    # An owned timer, stopped below: a QTimer.singleShot(…, loop.quit) would
    # fire seconds later on this loop after it is gone and crash the suite.
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(5000)
    loop.exec()
    timer.stop()
    deadline.stop()
    assert predicate()


class _Client:
    saved: ClassVar[list[dict]] = []

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    def status(self):
        return PluginStatus("unconfigured", "set a server")

    def get_config(self):
        return {"url": "https://a.example", "api_key": SECRET_MASK, "camera_model": ""}

    def set_config(self, values):
        _Client.saved.append(dict(values))
        if values.get("url") == "https://bad.example":
            return ConfigResult(False, {"url": "not reachable"})
        return ConfigResult(True, {})


INDEX = json.dumps({"version": 1, "plugins": [
    {"id": "io.example.demo", "name": "Demo photos", "description": "Photos.",
     "repo": URL, "ref": "v0.1.0", "capabilities": ["photos"], "icon": "folder-pictures"},
    {"id": "io.example.cal", "name": "Calendar", "repo": URL + "-cal", "ref": None,
     "capabilities": ["calendar"]},
]}).encode()


@pytest.fixture
def plugins(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runner = FakeRunner()
    manager = PluginManager(data_home=tmp_path / "data", settings_path=tmp_path / "p.json",
                            runner=runner, python="/usr/bin/python3")
    index = PluginIndex(fetch=lambda _url: INDEX, cache=tmp_path / "cache")
    _Client.saved = []
    settings = PluginSettings(manager=lambda: manager, index=lambda: index, client=_Client)
    return settings, manager, runner


def _idle(settings):
    return settings.state()["busy"] == ""


def test_store_install_is_confirmed_before_pip_runs(plugins) -> None:
    settings, _manager, runner = plugins
    settings.load_store(False)
    _wait(lambda: settings.state()["store"]["loaded"])
    cards = {card["id"]: card for card in settings.state()["store"]["items"]}
    assert cards["io.example.demo"]["state"] == "install"
    assert cards["io.example.cal"]["state"] == "soon" and not cards["io.example.cal"]["installable"]
    settings.install_from_store("io.example.cal")  # coming soon: nothing happens
    assert _idle(settings)
    settings.install_from_store("io.example.demo")
    _wait(lambda: settings.state()["pending"] != {})
    rows = {row["label"]: row["value"] for row in settings.state()["pending"]["rows"]}
    assert rows["Source"] == URL and rows["Capabilities"] == "photos"
    assert not any("pip" in " ".join(call) for call in runner.calls)
    settings.confirm_install()
    _wait(lambda: _idle(settings) and settings.state()["plugins"])
    assert "Installed Demo photos v0.1.0" in settings.state()["message"]
    row = settings.state()["plugins"][0]
    assert row["managed"] and row["source"] == URL and row["state"] == "unconfigured"
    _wait(lambda: settings.state()["store"]["items"][0]["state"] == "installed")


def test_cancel_discards_the_checkout_and_errors_are_messages(plugins) -> None:
    settings, manager, _runner = plugins
    settings.prepare_install(URL, "")
    _wait(lambda: settings.state()["pending"] != {})
    settings.cancel_install()
    assert settings.state()["pending"] == {}
    _wait(lambda: not list((manager.root / "src").glob(".staging-*")))
    settings.prepare_install("http://insecure.example/x", "")
    _wait(lambda: settings.state()["message"] != "")
    assert settings.state()["messageOk"] is False and "https" in settings.state()["message"]


def test_settings_form_round_trip_and_enable_switch(plugins) -> None:
    settings, manager, _runner = plugins
    manager.commit(manager.prepare(URL))
    settings.load()
    _wait(lambda: settings.state()["loaded"] and _idle(settings))
    settings.load_config("io.example.demo")
    _wait(lambda: settings.state()["config"].get("loaded"))
    fields = {field["key"]: field for field in settings.state()["config"]["fields"]}
    assert fields["url"]["value"] == "https://a.example"
    settings.save_config("io.example.demo", {"url": "https://bad.example"})
    _wait(lambda: settings.state()["config"].get("errors"))
    assert settings.state()["config"]["errors"] == {"url": "not reachable"}
    settings.save_config("io.example.demo", {"url": "https://b.example"})
    _wait(lambda: settings.state()["config"] == {})
    assert _Client.saved[-1] == {"url": "https://b.example"}
    _wait(lambda: _idle(settings))
    settings.set_enabled("io.example.demo", False)
    _wait(lambda: _idle(settings) and settings.state()["plugins"][0]["enabled"] is False)
    assert manager.disabled() == {"io.example.demo"}
    settings.set_indexes(["http://nope"])
    assert settings.state()["messageOk"] is False


def test_controller_features_and_doctor(app, monkeypatch) -> None:
    from blueferry.qt.controller import BridgeController
    from tests.test_qt_controller import _Backend

    class Backend(_Backend):
        def features(self):
            return {"calls_enabled": {"value": True, "restart_required": True}}

        def set_feature(self, name, enabled):
            self.feature = (name, enabled)
            return "restart-required"

    backend = Backend()
    controller = BridgeController(
        backend=backend, setup=object(), subscribe=False, autostart=False,
        doctor=lambda: DoctorReport(True, True, "WARNING something"),
    )
    monkeypatch.setattr(
        controller, "_run",
        lambda operation, on_done=None, *_args, **_kwargs: on_done(operation()),
    )
    controller.loadFeatures()
    assert controller.features["available"] is True
    assert controller.features["items"]["calls_enabled"]["restart_required"] is True
    controller.setFeature("calls_enabled", False)
    assert backend.feature == ("calls_enabled", False)
    assert "Restart" in controller.features["notice"]
    controller.runDoctor()
    _wait(lambda: controller.doctor["ran"])
    assert controller.doctor["warnings"] is True and controller.doctor["text"] == "WARNING something"


def test_doctor_output_is_plain_text() -> None:
    report = parse_output(1, "\x1b[31mERROR\x1b[0m bad\x07\n")
    assert report.text == "ERROR bad" and not report.ok
