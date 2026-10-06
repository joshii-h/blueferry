"""Terminal Photos screen: the newest items from a photos plugin.

Enter downloads the original and opens it with the desktop default viewer
(the companion tools' launcher); ``c`` downloads it and copies its path.
Plugin calls run on worker threads; every label is plain text.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from blueferry import companion_tools, photos_view
from blueferry import tui_design as design
from blueferry.plugin_api.client import Photo, PluginError
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.text_safety import terminal_text

Loader = Callable[[], tuple[PluginManifest | None, photos_view.PhotosSnapshot]]
Fetcher = Callable[[PluginManifest, str], Path]


def _load() -> tuple[PluginManifest | None, photos_view.PhotosSnapshot]:
    manifest = photos_view.find_plugin()
    return manifest, photos_view.load_recent(manifest)


def _plain(value: object) -> str:
    return terminal_text(value).replace("\n", " ")


def photo_line(photo: Photo) -> str:
    when = photos_view.taken_text(photo) or "unknown date"
    cached = "  ✓" if photo.original is not None else ""
    return f"{when}  {photos_view.type_text(photo)}{cached}"


class PhotosScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,g", "close", "Close", show=False),
        Binding("c", "copy_path", "Copy path", show=False),
        Binding("r", "reload", "Refresh", show=False),
        Binding("f", "filter", "All/Photos/Videos", show=False),
    ]

    def __init__(
        self,
        *,
        load: Loader = _load,
        fetch: Fetcher | None = None,
        system: companion_tools.System | None = None,
    ) -> None:
        super().__init__()
        self._load = load
        self._fetch = fetch or (
            lambda manifest, photo_id: photos_view.fetch_original(manifest, photo_id)
        )
        self._system = system or companion_tools.default_system()
        self._manifest: PluginManifest | None = None
        self._photos: list[Photo] = []
        self._all_photos: list[Photo] = []
        self.filter = "all"

    def compose(self) -> ComposeResult:
        with Vertical(id="photos-dialog", classes="dialog"):
            yield Static("Photos", classes="dialog-title")
            yield Static(design.section(design.RECENT_PHOTOS, design.PHOTO_FILTERS, "all"),
                         id="photos-title", classes="section-title")
            yield Static("Loading…", id="photos-hint", classes="dialog-copy")
            yield OptionList(id="photos-list")
            yield Static(design.key_hints(
                ("Enter", "open"), ("c", "copy path"), ("f", "All/Photos/Videos"),
                ("r", "refresh"), ("Esc", "close"),
            ), classes="key-hints")
            with Horizontal(classes="dialog-actions"):
                yield Button("Close", id="photos-close")

    def on_mount(self) -> None:
        self.reload()

    @work(thread=True, exclusive=True, group="photos-load", exit_on_error=False)
    def reload(self) -> None:
        manifest, snapshot = self._load()
        self.app.call_from_thread(self._show, manifest, snapshot)

    def _show(self, manifest: PluginManifest | None, snapshot: photos_view.PhotosSnapshot) -> None:
        self._manifest = manifest
        self._all_photos = list(snapshot.photos)
        self.query_one("#photos-hint", Static).update(_plain(snapshot.hint))
        self._render_photos()

    def _render_photos(self) -> None:
        self._photos = [
            photo for photo in self._all_photos
            if self.filter == "all" or (photo.type == "video") == (self.filter == "videos")
        ]
        self.query_one("#photos-title", Static).update(
            design.section(design.RECENT_PHOTOS, design.PHOTO_FILTERS, self.filter))
        options = self.query_one("#photos-list", OptionList)
        options.clear_options()
        options.add_options([Option(photo_line(photo), id=photo.id) for photo in self._photos])
        options.display = bool(self._photos)
        if self._photos:
            options.highlighted = 0
            options.focus()

    def _selected(self) -> Photo | None:
        index = self.query_one("#photos-list", OptionList).highlighted
        if index is None or not 0 <= index < len(self._photos) or self._manifest is None:
            return None
        return self._photos[index]

    @on(OptionList.OptionSelected, "#photos-list")
    def open_selected(self) -> None:
        photo = self._selected()
        if photo is not None:
            self._download(photo, copy=False)

    def action_copy_path(self) -> None:
        photo = self._selected()
        if photo is not None:
            self._download(photo, copy=True)

    def action_reload(self) -> None:
        self.reload()

    def action_filter(self) -> None:
        self.filter = design.next_filter(design.PHOTO_FILTERS, self.filter)
        self._render_photos()

    @work(thread=True, group="photos-fetch", exit_on_error=False)
    def _download(self, photo: Photo, *, copy: bool) -> None:
        manifest = self._manifest
        if manifest is None:
            return
        self.app.call_from_thread(self.notify, "Downloading…")
        try:
            path = self._fetch(manifest, photo.id)
        except PluginError as error:
            self.app.call_from_thread(self.notify, _plain(error), severity="error", markup=False)
            return
        if copy:
            self.app.call_from_thread(self.app.copy_to_clipboard, str(path))
            self.app.call_from_thread(
                self.notify, f"Copied {_plain(path)}", markup=False,
            )
            return
        try:
            self._system.open_uri(path.as_uri())
        except Exception as error:  # a desktop without a viewer must not end the TUI
            self.app.call_from_thread(
                self.notify, f"Could not open: {_plain(error)}", severity="error", markup=False,
            )
            return
        self.app.call_from_thread(self.notify, f"Opened {_plain(path.name)}", markup=False)

    @on(Button.Pressed, "#photos-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
