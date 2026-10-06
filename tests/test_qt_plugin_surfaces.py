"""Qt "From Plugins" and "Send to…": adapter and QML section, fakes only."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from blueferry import plugin_surfaces as surfaces
from blueferry.plugin_api.surfaces import Action, CardItem
from blueferry.qt.plugin_surfaces import PluginSurfaces

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def _wait(predicate) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert predicate()


CARDS = [
    surfaces.PluginCard("io.example.calendar", "Calendar", True, "", [
        CardItem("next", "Dentist", icon="view-calendar", subtitle="10:00", actions=[
            Action("open", "Open", kind="primary"), Action("snooze", "Snooze")])]),
    surfaces.PluginCard("io.example.broken", "Broken", False, "Unavailable: timeout"),
]


def test_reload_invoke_and_send_run_off_the_ui_thread(app, tmp_path) -> None:
    opened: list[str] = []
    invoked: list[tuple] = []
    sent: list[tuple] = []
    choice = surfaces.ShareChoice("io.example.ls", "LocalSend", "iphone", "iPhone", "smartphone")
    adapter = PluginSurfaces(
        load_cards=lambda: CARDS,
        load_targets=lambda: surfaces.ShareTargets([choice], ["Other: timeout"]),
        find=lambda plugin_id, capability: plugin_id,
        invoke=lambda plugin, item, action: invoked.append((plugin, item, action))
        or surfaces.Outcome(True, "Opened", "https://example.org/e"),
        send=lambda target, files: sent.append((target.key, files))
        or surfaces.Outcome(True, "Sending 1 file"),
        opener=opened.append,
    )
    adapter.reload()
    _wait(lambda: adapter.state()["loaded"])
    cards = adapter.state()["cards"]
    assert cards[0]["items"][0]["actions"][0] == {
        "id": "open", "label": "Open", "icon": "", "primary": True}
    assert cards[1]["ok"] is False and cards[1]["hint"] == "Unavailable: timeout"

    adapter.invoke("io.example.calendar", "next", "open")
    _wait(lambda: adapter.state()["message"] == "Opened")
    assert invoked == [("io.example.calendar", "next", "open")]
    assert opened == ["https://example.org/e"]

    adapter.load_targets()
    _wait(lambda: adapter.state()["targetsLoaded"])
    assert adapter.state()["targets"] == [
        {"key": "io.example.ls:iphone", "icon": "smartphone", "label": "iPhone"}]
    payload = tmp_path / "a.jpg"
    payload.write_bytes(b"x")
    adapter.send("io.example.ls:iphone", [QUrl.fromLocalFile(str(payload)).toString()])
    _wait(lambda: adapter.state()["message"] == "Sending 1 file")
    assert sent == [("io.example.ls:iphone", [str(payload.resolve())])]
    adapter.send("io.example.ls:iphone", ["/no/such/file"])
    assert "no such file" in adapter.state()["message"]
    adapter.send("gone:target", [str(payload)])
    assert adapter.state()["messageOk"] is False


def test_a_crashing_loader_never_breaks_the_card(app) -> None:
    def boom():
        raise RuntimeError("plugin exploded")

    adapter = PluginSurfaces(load_cards=boom)
    adapter.reload()
    _wait(lambda: adapter.state()["loaded"])
    assert adapter.state()["cards"] == [] and adapter.state()["messageOk"] is False


_FIND_JS = """(function find(item, name) {
    const children = item.children || [];
    for (let index = 0; index < children.length; ++index) {
        const child = children[index];
        if (child.objectName === name) return child;
        const found = find(child, name);
        if (found) return found;
    }
    return null;
})"""


def _find(engine, root, name):
    """Repeater delegates are visual children only; search them in QML."""
    found = engine.evaluate(_FIND_JS).call([engine.newQObject(root), name])
    return found.toQObject() if found.isQObject() else None


BRIDGE = """
import QtQuick
QtObject {
    property var calls: []
    property var pluginSurfaces: ({cards: %s, busy: "", message: "", messageOk: true})
    function refreshPluginCards() { calls = calls.concat(["refresh"]) }
    function invokePluginAction(p, i, a) { calls = calls.concat([p + "/" + i + "/" + a]) }
    function clearPluginCardMessage() {}
}
"""


def test_section_renders_items_hints_and_runs_actions(app) -> None:
    import json

    engine = QQmlEngine()
    bridge_component = QQmlComponent(engine)
    bridge_component.setData((BRIDGE % json.dumps(surfaces.card_rows(CARDS))).encode(), QUrl())
    bridge = bridge_component.create()
    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(ROOT / "src/blueferry/qt/qml/PluginCardSection.qml")))
    section = component.createWithInitialProperties({"bridge": bridge})
    assert section is not None, [e.toString() for e in component.errors()]
    assert section.property("visible") is True
    item = _find(engine, section, "pluginItem_next")
    assert item.property("title") == "Dentist"
    hint = _find(engine, section, "pluginCardHint")
    assert hint is not None
    item.clicked.emit()
    button = _find(engine, item, "pluginAction_snooze")
    button.clicked.emit()
    assert bridge.property("calls").toVariant() == [
        "refresh", "io.example.calendar/next/open", "io.example.calendar/next/snooze"]
    section.deleteLater()
    bridge.deleteLater()
