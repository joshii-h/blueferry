"""Qt adapter for the plugin surfaces (PLUGINS.md, ApiVersion 1.2).

The phone card's "From Plugins" section (capability ``card``) and "Send to…"
(capability ``share``) call plugins over D-Bus with timeouts, so every call
runs on a private pool; the D-Bus worker of the controller and the UI thread
never wait on a plugin. A content-free ``Plugin1.CardChanged`` from any
plugin schedules one coalesced reload. QML sees one plain ``state`` map.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import SLOT, QObject, QThreadPool, QTimer, QUrl, Signal, Slot

from blueferry import plugin_surfaces as surfaces
from blueferry.i18n import _
from blueferry.plugin_api import OBJECT_PATH, SIGNAL_CARD_CHANGED, SURFACES_INTERFACE
from blueferry.plugin_api.surfaces import checked_share_paths
from blueferry.qt.tasks import Task

RELOAD_DELAY_MS = 400


def open_url(uri: str) -> None:
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(uri))


class PluginSurfaces(QObject):
    changed = Signal()

    def __init__(
        self,
        *,
        load_cards: Callable[[], list[surfaces.PluginCard]] = surfaces.load_cards,
        load_targets: Callable[[], surfaces.ShareTargets] = surfaces.load_targets,
        invoke: Callable[..., surfaces.Outcome] = surfaces.invoke,
        find: Callable[[str, str], Any] = surfaces.find_plugin,
        send: Callable[..., surfaces.Outcome] = surfaces.send,
        opener: Callable[[str], None] = open_url,
        bus: Any = None,
        pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_cards = load_cards
        self._load_targets = load_targets
        self._invoke = invoke
        self._find = find
        self._send = send
        self._open = opener
        self._pool = pool or QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._tasks: set[Task] = set()
        self._choices: dict[str, surfaces.ShareChoice] = {}
        self._loading = False
        self._reload_again = False
        self._state: dict[str, Any] = {
            "loaded": False, "cards": [], "busy": "", "message": "", "messageOk": True,
            "targets": [], "targetsLoaded": False, "targetProblems": [],
        }
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RELOAD_DELAY_MS)
        self._timer.timeout.connect(self.reload)
        if bus is not None:
            # Any plugin may say "my card changed"; the signal carries nothing
            # and only schedules one reload, which checks every plugin again.
            bus.connect("", OBJECT_PATH, SURFACES_INTERFACE, SIGNAL_CARD_CHANGED,
                        self, SLOT("_cardChanged()"))

    def state(self) -> dict:
        return dict(self._state)

    def _update(self, **values: Any) -> None:
        self._state = {**self._state, **values}
        self.changed.emit()

    def _run(self, work: Callable[[], object], done: Callable[[Any], None],
             failed: Callable[[str], None] | None = None) -> None:
        task = Task(work)
        self._tasks.add(task)
        task.signals.done.connect(done)
        task.signals.failed.connect(failed or (lambda message: self._say(
            message or _("The plugin did not answer."), False)))
        task.signals.finished.connect(lambda: self._tasks.discard(task))
        self._pool.start(task)

    def _say(self, message: str, ok: bool) -> None:
        self._update(message=message, messageOk=ok, busy="")

    # ---- card ---------------------------------------------------------------------

    @Slot()
    def _cardChanged(self) -> None:
        self._timer.start()

    def reload(self) -> None:
        """Fetch every card plugin's items again (coalesced)."""
        if self._loading:
            self._reload_again = True
            return
        self._loading = True

        def done(cards: list[surfaces.PluginCard]) -> None:
            self._loading = False
            self._update(loaded=True, cards=surfaces.card_rows(cards))
            if self._reload_again:
                self._reload_again = False
                self._timer.start()

        def failed(message: str) -> None:
            self._loading = False
            self._update(loaded=True)
            self._say(message or _("Plugins are unavailable."), False)

        self._run(self._load_cards, done, failed)

    def invoke(self, plugin_id: str, item_id: str, action_id: str) -> None:
        if self._state["busy"]:
            return
        self._update(busy=f"{plugin_id}:{item_id}:{action_id}", message="")

        def work() -> surfaces.Outcome:
            return self._invoke(self._find(plugin_id, "card"), item_id, action_id)

        def done(outcome: surfaces.Outcome) -> None:
            self._say(outcome.message, outcome.ok)
            if outcome.ok and outcome.open_uri:
                self._open(outcome.open_uri)
            self._timer.start()

        self._run(work, done)

    def clear_message(self) -> None:
        self._update(message="")

    # ---- share ------------------------------------------------------------------

    def load_targets(self) -> None:
        def done(targets: surfaces.ShareTargets) -> None:
            self._choices = {choice.key: choice for choice in targets.choices}
            self._update(
                targetsLoaded=True,
                targets=[
                    {"key": choice.key, "icon": choice.icon or "document-send",
                     "label": surfaces.choice_label(choice, targets.choices)}
                    for choice in targets.choices
                ],
                targetProblems=list(targets.problems),
            )

        self._run(self._load_targets, done)

    def send(self, key: str, urls: Sequence[object]) -> None:
        choice = self._choices.get(key)
        if choice is None:
            self._say(_("That target is gone; open “Send to” again."), False)
            return
        paths = [QUrl(str(url)).toLocalFile() if str(url).startswith("file:") else str(url)
                 for url in urls]
        try:
            files = checked_share_paths(paths)
        except ValueError as error:
            self._say(str(error), False)
            return
        self._update(busy=f"send:{key}", message="")

        def done(outcome: surfaces.Outcome) -> None:
            self._say(outcome.message, outcome.ok)
            self._timer.start()

        self._run(lambda: self._send(choice, files), done)
