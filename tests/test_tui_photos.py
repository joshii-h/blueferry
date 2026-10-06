"""The terminal Photos screen with a fake plugin and launcher."""
from __future__ import annotations

from textual.widgets import OptionList

from blueferry import photos_view
from blueferry.companion_tools import System
from blueferry.plugin_api.client import Photo, PluginError
from blueferry.plugin_api.testing import manifest
from blueferry.tui import BlueFerryApp, TuiState
from blueferry.tui_photos import PhotosScreen, photo_line
from tests.test_tui_phone import _Backend, _plain, _run, _until


def test_photos_screen_opens_and_copies_originals(tmp_path, monkeypatch) -> None:
    original = tmp_path / "IMG_0001.HEIC"
    original.write_bytes(b"x")
    snapshot = photos_view.PhotosSnapshot(True, True, "2 recent items from photos.example.org", [
        Photo("a", "2026-10-06T16:21:00Z", "image", None),
        Photo("b", "", "video", None, original),
    ])
    monkeypatch.setattr(photos_view, "find_plugin", lambda: manifest())
    monkeypatch.setattr(photos_view, "load_recent", lambda _manifest: snapshot)
    fetched: list[str] = []

    def fetch(_manifest, photo_id):
        fetched.append(photo_id)
        if photo_id == "b":
            raise PluginError("the Immich server is not reachable")
        return original

    monkeypatch.setattr(photos_view, "fetch_original", fetch)
    opened: list[str] = []
    copied: list[str] = []

    async def scenario() -> None:
        app = BlueFerryApp(
            TuiState(_Backend()), monitor_factory=lambda: None,
            companion_system=System(open_uri=opened.append),
        )
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await _until(pilot, lambda: app.state.status.calls_enabled)
            app.set_focus(None)
            await pilot.press("g")
            await _until(pilot, lambda: isinstance(app.screen, PhotosScreen))
            await _until(pilot, lambda: "2 recent items" in _plain(app, "#photos-hint"))
            options = app.screen.query_one("#photos-list", OptionList)
            assert options.option_count == 2
            await pilot.press("enter")
            await _until(pilot, lambda: opened == [original.as_uri()])
            await pilot.press("c")
            await _until(pilot, lambda: copied == [str(original)])
            await pilot.press("down", "enter")
            await _until(pilot, lambda: fetched == ["a", "a", "b"])
            assert len(opened) == 1
            await pilot.press("escape")
            await _until(pilot, lambda: not isinstance(app.screen, PhotosScreen))

    _run(scenario())


def test_photos_screen_shows_the_setup_hint_without_a_plugin(monkeypatch) -> None:
    monkeypatch.setattr(photos_view, "find_plugin", lambda: None)

    async def scenario() -> None:
        app = BlueFerryApp(TuiState(_Backend()), monitor_factory=lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(PhotosScreen())
            await _until(pilot, lambda: bool(app.screen.query("#photos-hint")))
            await _until(pilot, lambda: "plugins immich setup" in _plain(app, "#photos-hint"))
            assert app.screen.query_one("#photos-list", OptionList).display is False

    _run(scenario())


def test_photo_lines_are_plain() -> None:
    assert photo_line(Photo("a", "", "other", None)) == "unknown date  File"
