"""Qt "From Plugins" and "Send to…": adapter and QML section, fakes only."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QTimer, QUrl
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
        share_available=lambda: True,
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
    assert adapter.state()["shareAvailable"] is True
    assert adapter.state()["targetsLoaded"] is False  # targets start plugins: not yet
    assert cards[0]["items"][0]["actions"][0] == {
        "id": "open", "label": "Open", "icon": "", "primary": True, "sendTo": ""}
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
    # FileDialog.selectedFiles delivers QUrl objects.
    adapter.send("io.example.ls:iphone", [QUrl.fromLocalFile(str(payload))])
    _wait(lambda: len(sent) == 2)
    assert sent[1] == ("io.example.ls:iphone", [str(payload.resolve())])
    adapter.send("io.example.ls:iphone", ["/no/such/file"])
    _wait(lambda: "no such file" in adapter.state()["message"])
    adapter.send("gone:target", [str(payload)])
    assert adapter.state()["messageOk"] is False


def test_a_crashing_loader_never_breaks_the_card(app) -> None:
    def boom():
        raise RuntimeError("plugin exploded")

    adapter = PluginSurfaces(load_cards=boom, share_available=lambda: False)
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


SEND_CARDS = [
    surfaces.PluginCard("io.example.ls", "LocalSend", True, "", [
        CardItem("dev-phone", "iPhone", icon="phone", subtitle="iPhone · verified", actions=[
            Action("send", "Send files…", "document-send", "primary", send_to="ls-1"),
            Action("trust", "Always accept")]),
    ], can_send=True),
]

SEND_BRIDGE = """
import QtQuick
QtObject {
    property var calls: []
    property var pluginSurfaces: ({cards: %s, busy: "", message: "", messageOk: true})
    function refreshPluginCards() {}
    function invokePluginAction(p, i, a) { calls = calls.concat([p + "/" + i + "/" + a]) }
    function sendFromPluginCard(p, t, l, urls) {
        calls = calls.concat(["send " + p + "/" + t + "/" + l + "/" + urls.length])
    }
    function clearPluginCardMessage() {}
}
"""


def test_card_rows_carry_send_targets_only_for_share_plugins() -> None:
    rows = surfaces.card_rows(SEND_CARDS)
    item = rows[0]["items"][0]
    assert item["dropTarget"] == "ls-1"
    assert [a["sendTo"] for a in item["actions"]] == ["ls-1", ""]
    without_share = [surfaces.PluginCard("io.example.ls", "LocalSend", True, "",
                                         SEND_CARDS[0].items)]
    item = surfaces.card_rows(without_share)[0]["items"][0]
    assert item["dropTarget"] == "" and item["actions"][0]["sendTo"] == ""


def test_a_sending_action_opens_files_and_sends_instead_of_invoking(app) -> None:
    import json

    engine = QQmlEngine()
    bridge_component = QQmlComponent(engine)
    bridge_component.setData(
        (SEND_BRIDGE % json.dumps(surfaces.card_rows(SEND_CARDS))).encode(), QUrl())
    bridge = bridge_component.create()
    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(ROOT / "src/blueferry/qt/qml/PluginCardSection.qml")))
    section = component.createWithInitialProperties({"bridge": bridge})
    assert section is not None, [e.toString() for e in component.errors()]
    item = _find(engine, section, "pluginItem_dev-phone")
    assert _find(engine, item, "pluginDropArea").property("enabled") is True
    item.clicked.emit()  # the primary action is the sending one
    pending = section.property("pendingSend").toVariant()
    assert pending == {"pluginId": "io.example.ls", "target": "ls-1", "label": "iPhone"}
    QMetaObject.invokeMethod(section, "finishSend",
                             Q_ARG("QVariant", ["file:///tmp/a.jpg", "file:///tmp/b"]))
    _find(engine, item, "pluginAction_trust").clicked.emit()
    assert bridge.property("calls").toVariant() == [
        "send io.example.ls/ls-1/iPhone/2", "io.example.ls/dev-phone/trust"]
    assert section.property("pendingSend") is None or (
        section.property("pendingSend").toVariant() is None)
    section.deleteLater()
    bridge.deleteLater()


def test_send_from_card_resolves_the_target_off_the_ui_thread(app, tmp_path) -> None:
    picked = tmp_path / "a.txt"
    picked.write_text("x")
    sent: list[tuple] = []
    choice = surfaces.ShareChoice("io.example.ls", "LocalSend", "ls-1", "iPhone")
    asked: list[tuple] = []
    adapter = PluginSurfaces(
        load_cards=lambda: [], share_available=lambda: True,
        card_choice=lambda p, t, label: asked.append((p, t, label)) or choice,
        send=lambda target, files: sent.append((target.key, files))
        or surfaces.Outcome(True, "Sending 1 file to iPhone"),
    )
    adapter.send_from_card("io.example.ls", "ls-1", "iPhone", [QUrl.fromLocalFile(str(picked))])
    _wait(lambda: adapter.state()["message"] == "Sending 1 file to iPhone")
    assert asked == [("io.example.ls", "ls-1", "iPhone")]
    assert sent == [("io.example.ls:ls-1", [str(picked)])]

    gone = PluginSurfaces(load_cards=lambda: [], share_available=lambda: True,
                          card_choice=lambda *_args: None)
    gone.send_from_card("io.example.ls", "ls-1", "iPhone", [str(picked)])
    _wait(lambda: gone.state()["message"] != "")
    assert gone.state()["messageOk"] is False


def test_a_stale_card_target_says_to_refresh(app, tmp_path) -> None:
    picked = tmp_path / "a.txt"
    picked.write_text("x")

    def stale(*_args):
        raise LookupError("iPhone is no longer there. Refresh the card and try again.")

    adapter = PluginSurfaces(load_cards=lambda: [], share_available=lambda: True,
                             card_choice=stale)
    adapter.send_from_card("io.example.ls", "ls-1", "iPhone", [str(picked)])
    _wait(lambda: adapter.state()["message"] != "")
    assert adapter.state()["messageOk"] is False and "Refresh" in adapter.state()["message"]
