"""Qt adapter for the companion tools: worker hand-off and state, fake system."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication

from blueferry.companion_tools import CommandResult
from blueferry.qt.companion import CompanionTools
from tests.test_companion_tools import DEVICE, PHOTO_TOOLS, Fake


@pytest.fixture(scope="module")
def app():
    # A GUI application: later Qt tests cannot replace a plain QCoreApplication.
    return QCoreApplication.instance() or QGuiApplication([])


def _wait(tools: CompanionTools, predicate) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert predicate()


def test_refresh_probes_on_the_worker_and_exposes_rows(app, tmp_path) -> None:
    fake = Fake(tmp_path, installed={"uxplay", *PHOTO_TOOLS}, answers=DEVICE)
    tools = CompanionTools(fake.system())
    assert tools.state()["tools"] == [] and fake.ran == []  # nothing on construction
    tools.refresh()
    _wait(tools, lambda: tools.state()["probed"])
    rows = {row["key"]: row for row in tools.state()["tools"]}
    assert rows["mirror"]["enabled"] and rows["photos"]["enabled"]
    assert not rows["send"]["installed"] and not rows["eject"]["enabled"]


def test_blocking_actions_run_one_at_a_time_and_report(app, tmp_path) -> None:
    fake = Fake(tmp_path, installed=PHOTO_TOOLS, answers={
        **DEVICE, ("idevicepair", "validate"): CommandResult(1, "not paired"),
    })
    tools = CompanionTools(fake.system())
    reports: list[tuple[bool, str]] = []
    tools.reported.connect(lambda ok, message: reports.append((ok, message)))
    tools.run("photos")
    assert tools.state()["busy"] == "photos"
    tools.run("eject")  # ignored while busy
    _wait(tools, lambda: tools.state()["busy"] == "" and reports)
    assert tools.state()["needsPairing"] is True
    assert reports[0][0] is False and "Trust This Computer" in reports[0][1]
    assert ["fusermount3", "-u"] not in [argv[:2] for argv in fake.ran]

    fake.answers[("idevicepair", "pair")] = CommandResult(0, "SUCCESS")
    tools.run("pair")
    _wait(tools, lambda: len(reports) == 2 and tools.state()["busy"] == "")
    assert tools.state()["needsPairing"] is False
    tools.clear_message()
    assert tools.state()["message"] == ""


def test_mirroring_starts_immediately_without_the_worker(app, tmp_path) -> None:
    fake = Fake(tmp_path, installed={"uxplay"})
    tools = CompanionTools(fake.system())
    tools.run("mirror")
    assert list(fake.commands) == [("uxplay", "-n", "battlestation", "-nh")]
    assert tools.state()["messageOk"] is True
    _wait(tools, lambda: tools.state()["probed"])
