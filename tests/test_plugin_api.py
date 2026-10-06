"""blueferry.plugin_api: manifests, the validating client, the service base."""
from __future__ import annotations

import ast
import json
import os
import threading
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from blueferry.plugin_api import MAX_REPLY_BYTES
from blueferry.plugin_api.client import PluginClient, PluginError
from blueferry.plugin_api.manifest import (
    ManifestError,
    bus_name_for,
    discover,
    parse_manifest,
)
from blueferry.plugin_api.service import PhotosService, RateLimitedError
from blueferry.plugin_api.testing import (
    ScriptedTransport,
    ServiceTransport,
    inline_service,
    manifest,
)

VALID = """# comment
[Desktop Entry]
Name=ignored
[BlueFerry Plugin]
Id=io.example.immich-photos
Name=Immich photos
Name[de]=Immich-Fotos
Version=0.1.0
ApiVersion=1
MinBlueFerry=0.8
Capabilities=photos;future-thing;
Homepage=https://example.org/immich
Source=https://git.example.org/immich.git
Exec=python3 -m example_plugin serve
Cli=python3 -m example_plugin
Alias=immich
"""


def test_manifest_parses_required_and_optional_keys() -> None:
    parsed = parse_manifest(VALID, blueferry_version="0.8.1")
    assert parsed.id == "io.example.immich-photos"
    assert parsed.bus_name == "io.weirdware.BlueFerry.Plugin.io.example.immich_photos"
    assert parsed.capabilities == ("photos",)
    assert parsed.exec == ("python3", "-m", "example_plugin", "serve")
    assert parsed.cli[-1] == "example_plugin" and parsed.alias == "immich"


@pytest.mark.parametrize("change,reason", [
    (("ApiVersion=1", "ApiVersion=2"), "plugin API 2"),
    (("ApiVersion=1", "ApiVersion=x"), "integer"),
    (("MinBlueFerry=0.8", "MinBlueFerry=9.0"), "needs BlueFerry 9.0"),
    (("Id=io.example.immich-photos", "Id=photos"), "reverse-DNS"),
    (("Id=io.example.immich-photos", "Id=io.9bad.x"), "reverse-DNS"),
    (("Capabilities=photos;future-thing;", "Capabilities=nope;"), "no capability"),
    (("Homepage=https://example.org/immich", "Homepage=http://example.org"), "https"),
    (("Exec=python3 -m example_plugin serve", "Exec='unterminated"), "Exec"),
    (("Alias=immich", "Alias=Bad Alias"), "Alias"),
    (("Version=0.1.0\n", ""), "missing Version"),
])
def test_manifest_rejections_say_why(change, reason) -> None:
    with pytest.raises(ManifestError, match=reason):
        parse_manifest(VALID.replace(*change), blueferry_version="0.8.1")


def test_manifest_needs_a_homepage_or_source() -> None:
    text = VALID.replace("Homepage=https://example.org/immich\n", "").replace(
        "Source=https://git.example.org/immich.git\n", "")
    with pytest.raises(ManifestError, match="Homepage or Source"):
        parse_manifest(text)


@given(st.text(max_size=2000))
def test_manifest_parser_only_raises_manifest_errors(text) -> None:
    try:
        parse_manifest(text)
    except ManifestError:
        pass


def test_bus_name_replaces_hyphens() -> None:
    assert bus_name_for("io.a.b-c") == "io.weirdware.BlueFerry.Plugin.io.a.b_c"


def _write(directory: Path, name: str, text: str, mode: int = 0o644) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    path.chmod(mode)
    return path


def test_discovery_prefers_user_manifests_and_reports_ignored(tmp_path) -> None:
    user, system = tmp_path / "user", tmp_path / "system"
    _write(user, "a.plugin", VALID.replace("Name=Immich photos", "Name=Mine"))
    _write(system, "a.plugin", VALID)
    _write(system, "b.plugin", VALID.replace("ApiVersion=1", "ApiVersion=7"))
    _write(system, "c.plugin", VALID.replace("io.example.immich", "io.example.other"), 0o666)
    _write(system, "notes.txt", "x")
    found = discover([user, system, tmp_path / "missing"], blueferry_version="0.8.1")
    assert [plugin.name for plugin in found.plugins] == ["Mine"]
    reasons = dict(found.ignored)
    assert "plugin API 7" in reasons["b.plugin"]
    assert "another user" in reasons["c.plugin"]
    assert found.find("immich") is found.plugins[0]
    assert found.with_capability("photos") == found.plugins


# ---- client validation ---------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache" / "blueferry"
    (root / "immich").mkdir(parents=True)
    return root


def _client(replies, cache, **kwargs) -> PluginClient:
    return PluginClient(
        manifest(), transport=ScriptedTransport(replies, **kwargs), cache_root=lambda: cache,
    )


def test_list_recent_keeps_only_valid_entries_and_own_cache_files(cache, tmp_path) -> None:
    thumb = cache / "immich" / "a.webp"
    thumb.write_bytes(b"x")
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"x")
    escape = cache / "immich" / "escape.webp"
    escape.symlink_to(outside)
    reply = json.dumps([
        {"id": "a", "taken_at": "2026-10-06T18:21:00Z", "type": "image", "thumbnail": str(thumb)},
        {"id": "b", "taken_at": "<b>x</b>", "type": "evil", "thumbnail": str(outside)},
        {"id": "c", "taken_at": "2026-10-05", "type": "video", "thumbnail": str(escape)},
        {"id": "../etc", "type": "image"},
        {"id": "a", "type": "image"},
        "junk",
    ])
    photos = _client({"ListRecent": reply}, cache).list_recent(500)
    assert [photo.id for photo in photos] == ["a", "b", "c"]
    assert photos[0].thumbnail == thumb.resolve()
    assert photos[1].taken_at == "" and photos[1].type == "other"
    assert photos[1].thumbnail is None and photos[2].thumbnail is None


@pytest.mark.parametrize("reply,message", [
    ("x" * (MAX_REPLY_BYTES + 1), "oversized"),
    ("{not json", "invalid JSON"),
    (7, "invalid reply"),
    ('{"a": 1}', "invalid photo list"),
])
def test_bad_replies_are_errors_not_crashes(cache, reply, message) -> None:
    with pytest.raises(PluginError, match=message):
        _client({"ListRecent": reply}, cache).list_recent(5)


def test_a_plugin_owned_by_another_user_is_refused(cache) -> None:
    with pytest.raises(PluginError, match="different user"):
        _client({"Status": '{"state": "ok"}'}, cache, uid=os.getuid() + 1).status()


def test_status_and_fetch_original_are_validated(cache) -> None:
    original = cache / "immich" / "IMG_0001.HEIC"
    original.write_bytes(b"x")
    client = _client({
        "Status": json.dumps({"state": "unconfigured", "detail": "run\x1b[31m setup"}),
        "FetchOriginal": str(original),
    }, cache)
    status = client.status()
    assert status.state == "unconfigured" and "\x1b" not in status.detail
    assert client.fetch_original("abc") == original.resolve()
    with pytest.raises(PluginError):
        client.fetch_original("../x")
    with pytest.raises(PluginError, match="invalid status"):
        _client({"Status": '{"state": "pwned"}'}, cache).status()


# ---- service base --------------------------------------------------------------


class _Photos(PhotosService):
    def __init__(self, *args, files=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.files = files or {}

    def status(self) -> dict[str, object]:
        return {"state": "ok", "server": "photos.example.org"}

    def list_recent(self, limit):
        return [{"id": key, "taken_at": "2026-10-06", "type": "image", "thumbnail": path}
                for key, path in list(self.files.items())[:limit]]

    def fetch_original(self, photo_id):
        if photo_id not in self.files:
            raise KeyError(photo_id)  # never leaks into the reply text
        return self.files[photo_id]


def test_service_round_trip_through_the_harness(cache) -> None:
    path = cache / "immich" / "one.jpg"
    path.write_bytes(b"x")
    service = inline_service(_Photos, manifest(), files={"one": str(path)})
    client = PluginClient(manifest(), transport=ServiceTransport(service),
                          cache_root=lambda: cache)
    assert client.status().server == "photos.example.org"
    assert [photo.id for photo in client.list_recent(10)] == ["one"]
    assert client.fetch_original("one") == path.resolve()
    with pytest.raises(PluginError, match="KeyError"):
        client.fetch_original("two")
    info = json.loads(service.GetInfo(sender=":1.9"))
    assert info["api_version"] == 1 and info["capabilities"] == ["photos"]


def test_service_rate_limits_a_runaway_caller() -> None:
    now = [0.0]
    service = inline_service(_Photos, manifest(), clock=lambda: now[0])
    for _ in range(120):
        service.Status(sender=":1.5")
    with pytest.raises(RateLimitedError):
        service.Status(sender=":1.5")
    service.Status(sender=":1.6")
    now[0] = 61.0
    service.Status(sender=":1.5")


@pytest.mark.private_dbus
def test_service_answers_on_the_private_bus(cache) -> None:
    import dbus
    import dbus.mainloop
    import dbus.service
    from gi.repository import GLib

    from blueferry.plugin_api.client import DBusTransport
    from tests.private_bus import open_private_bus

    path = cache / "immich" / "p.jpg"
    path.write_bytes(b"x")
    plugin = manifest(f"io.example.t{os.getpid()}")
    bus = dbus.SessionBus()
    name = dbus.service.BusName(plugin.bus_name, bus=bus, do_not_queue=True)
    service = _Photos(plugin, bus, files={"p": str(path)}, start_worker=lambda work: work())
    outcome: dict = {}

    def request() -> None:
        connection = open_private_bus(mainloop=dbus.mainloop.NULL_MAIN_LOOP)
        try:
            client = PluginClient(plugin, transport=DBusTransport(connection),
                                  cache_root=lambda: cache)
            outcome["photos"] = client.list_recent(5)
            outcome["status"] = client.status()
        except Exception as error:
            outcome["error"] = error
        finally:
            connection.close()

    thread = threading.Thread(target=request)
    thread.start()
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.001)
    thread.join(1)
    try:
        assert "error" not in outcome, outcome
        assert outcome["photos"][0].thumbnail == path.resolve()
        assert outcome["status"].ready
    finally:
        service.remove_from_connection()
        del name
        bus.release_name(plugin.bus_name)


def test_plugin_api_imports_nothing_else_from_blueferry() -> None:
    """The package must stay separable into its own distribution."""
    root = Path(__file__).resolve().parents[1] / "src" / "blueferry" / "plugin_api"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert not (node.module or "").startswith("blueferry"), (path.name, node.module)
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("blueferry") for a in node.names), path.name
