"""Qt adapter for plugin management (settings category "Plugins").

Git, pip, the plugin indexes and calls into plugins all block, so they run
on a private single-thread pool; the D-Bus worker of the controller is never
held up. QML sees one plain ``state`` map (see :meth:`PluginSettings.state`)
and calls the slots the controller forwards. An install is always two
steps: ``prepareInstall`` fills ``state.pending`` with the source, ref,
capabilities and command, and only ``confirmInstall`` runs pip.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, Signal

from blueferry import plugin_settings_view as view
from blueferry.i18n import _
from blueferry.plugin_api.client import PluginClient
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.plugin_index import DEFAULT_INDEX_URL, PluginIndex, check_index_url, index_urls
from blueferry.plugin_manager import InstallError, PluginManager, PreparedInstall
from blueferry.qt.tasks import Task


class PluginSettings(QObject):
    changed = Signal()

    def __init__(
        self,
        *,
        manager: Callable[[], PluginManager] = PluginManager,
        index: Callable[[], PluginIndex] = PluginIndex,
        client: Callable[[PluginManifest], PluginClient] = PluginClient,
        pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager()
        self._index = index()
        self._client = client
        self._pool = pool or QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._tasks: set[Task] = set()
        self._queue: list[tuple] = []
        self._manifests: dict[str, PluginManifest] = {}
        self._prepared: PreparedInstall | None = None
        self._store_entries: dict[str, dict] = {}
        self._state: dict[str, Any] = {
            "loaded": False, "busy": "", "message": "", "messageOk": True,
            "plugins": [], "ignored": [], "pending": {}, "config": {},
            "store": {"loaded": False, "items": [], "problems": []},
            "indexes": [], "defaultIndex": DEFAULT_INDEX_URL,
        }

    def state(self) -> dict:
        return dict(self._state)

    # ---- plumbing ---------------------------------------------------------

    def _update(self, **values: Any) -> None:
        self._state = {**self._state, **values}
        self.changed.emit()

    def _work(
        self,
        busy: str,
        operation: Callable[[], object],
        done: Callable[[Any], None],
        *,
        keep_message: bool = False,
    ) -> None:
        """Run one blocking step on the pool; later steps wait their turn."""
        if self._state["busy"]:
            self._queue.append((busy, operation, done, keep_message))
            return
        self._update(busy=busy, **({} if keep_message else {"message": ""}))
        task = Task(operation)
        self._tasks.add(task)

        def finished(value: object) -> None:
            self._update(busy="")
            done(value)

        def failed(message: str) -> None:
            self._update(busy="", message=message or _("The plugin operation failed"),
                         messageOk=False)

        def next_step() -> None:
            self._tasks.discard(task)
            if self._queue and not self._state["busy"]:
                step, work, then, keep = self._queue.pop(0)
                self._work(step, work, then, keep_message=keep)

        task.signals.done.connect(finished)
        task.signals.failed.connect(failed)
        task.signals.finished.connect(next_step)
        self._pool.start(task)

    def _say(self, message: str, ok: bool = True) -> None:
        self._update(message=message, messageOk=ok)

    # ---- installed plugins --------------------------------------------------

    def load(self) -> None:
        def work() -> tuple:
            entries, ignored = self._manager.entries()
            statuses = view.plugin_statuses(entries, self._client)
            return entries, ignored, statuses, index_urls(self._manager.settings())

        def done(value: tuple) -> None:
            entries, ignored, statuses, indexes = value
            self._manifests = {entry.manifest.id: entry.manifest for entry in entries}
            self._update(
                loaded=True,
                plugins=[view.entry_row(e, statuses.get(e.manifest.id)) for e in entries],
                ignored=[{"name": name, "reason": reason} for name, reason in ignored],
                indexes=indexes,
            )

        self._work("list", work, done, keep_message=True)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._work("enable", lambda: self._manager.set_enabled(plugin_id, enabled),
                   lambda _value: self._reload())

    def _reload(self) -> None:
        self.load()

    # ---- install, update, remove ----------------------------------------------

    def _show_pending(self, kind: str, prepared: PreparedInstall | None) -> None:
        if prepared is None:
            self._say(_("The plugin is up to date."))
            return
        self._prepared = prepared
        self._update(pending={
            "kind": kind, "id": prepared.manifest.id, "name": prepared.manifest.name,
            "rows": view.summary_rows(prepared),
        })

    def prepare_install(self, url: str, ref: str = "") -> None:
        self.cancel_install()
        self._work("prepare", lambda: self._manager.prepare(url.strip(), ref.strip() or None),
                   lambda prepared: self._show_pending("install", prepared))

    def install_from_store(self, plugin_id: str) -> None:
        card = self._store_entries.get(plugin_id)
        if card is None or not card["installable"]:
            return
        if card["state"] == "update" and plugin_id in self._manifests:
            self.prepare_update(plugin_id)
            return
        self.prepare_install(card["repo"], card["ref"])

    def prepare_update(self, plugin_id: str) -> None:
        self.cancel_install()
        self._work("prepare", lambda: self._manager.prepare_update(plugin_id),
                   lambda prepared: self._show_pending("update", prepared))

    def confirm_install(self) -> None:
        prepared = self._prepared
        if prepared is None:
            return
        self._prepared = None
        kind = self._state["pending"].get("kind", "install")
        self._update(pending={})

        def done(record: Any) -> None:
            self._say(
                (_("Updated {name} to {ref}.") if kind == "update"
                 else _("Installed {name} {ref}.")).format(
                    name=prepared.manifest.name, ref=record.ref_label,
                ),
            )
            self._reload()
            self.load_store(False)

        self._work("install", lambda: self._manager.commit(prepared), done)

    def cancel_install(self) -> None:
        prepared, self._prepared = self._prepared, None
        if prepared is not None:
            # Deleting the checkout touches the disk; keep it off the UI thread.
            self._pool.start(Task(lambda: self._manager.discard(prepared)))
        if self._state["pending"]:
            self._update(pending={})

    def remove(self, plugin_id: str) -> None:
        def done(_value: object) -> None:
            self._say(_("Removed the plugin. Its own settings and keyring entries stay."))
            self._reload()

        self._work("remove", lambda: self._manager.remove(plugin_id), done)

    # ---- settings form --------------------------------------------------------

    def load_config(self, plugin_id: str) -> None:
        manifest = self._manifests.get(plugin_id)
        if manifest is None or not manifest.config:
            return

        def done(values: Mapping[str, object]) -> None:
            self._update(config={
                "id": plugin_id, "name": manifest.name, "loaded": True,
                "fields": view.form_fields(manifest, values), "errors": {},
            })

        self._update(config={"id": plugin_id, "name": manifest.name, "loaded": False,
                             "fields": view.form_fields(manifest, {}), "errors": {}})
        self._work("config", lambda: self._client(manifest).get_config(), done)

    def save_config(self, plugin_id: str, raw: Mapping[str, object]) -> None:
        manifest = self._manifests.get(plugin_id)
        if manifest is None:
            return
        values, errors = view.parse_form(manifest, raw)
        if errors:
            self._update(config={**self._state["config"], "errors": errors})
            return

        def done(result: Any) -> None:
            if result.ok:
                self._say(_("Saved the settings of {name}.").format(name=manifest.name))
                self._update(config={})
                self._reload()
            else:
                self._update(config={**self._state["config"], "errors": dict(result.errors)})

        self._work("config", lambda: self._client(manifest).set_config(values), done)

    def close_config(self) -> None:
        self._update(config={})

    # ---- store --------------------------------------------------------------------

    def load_store(self, refresh: bool) -> None:
        def work() -> object:
            entries, _ignored = self._manager.entries()
            return view.load_catalog(self._manager, self._index, entries, refresh=refresh)

        def done(catalog: Any) -> None:
            cards = [view.store_row(item) for item in catalog.items]
            self._store_entries = {card["id"]: card for card in cards}
            self._update(store={
                "loaded": True, "items": cards,
                "problems": [{"index": url, "problem": text} for url, text in catalog.problems],
            })

        self._work("store", work, done, keep_message=True)

    def set_indexes(self, urls: list[str]) -> None:
        try:
            cleaned = list(dict.fromkeys(check_index_url(str(url)) for url in urls if url))
        except InstallError as error:
            self._say(str(error), False)
            return

        def done(_value: object) -> None:
            self._update(indexes=cleaned)
            self.load_store(True)

        self._work("indexes", lambda: self._manager.update_settings(indexes=cleaned), done)

    def clear_message(self) -> None:
        self._update(message="")
