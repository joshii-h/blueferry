"""The plugin store index: untrusted JSON, cache with TTL, install state."""
from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from blueferry.plugin_index import (
    DEFAULT_INDEX_URL,
    MAX_INDEX_BYTES,
    IndexFetchError,
    PluginIndex,
    index_urls,
    parse_entry,
    parse_index,
    search,
)
from blueferry.plugin_manager import InstallRecord

IMMICH = {
    "id": "io.weirdware.blueferry.immich_photos", "name": "Immich photos",
    "description": "Recent photos from your own Immich server.",
    "repo": "https://github.com/joshii-h/blueferry-plugin-immich", "ref": "v0.2.0",
    "capabilities": ["photos"], "icon": "folder-pictures", "emoji": "📷",
    "min_blueferry": "0.8", "api_version": 1,
}
SOON = {
    "id": "io.weirdware.blueferry.calendar", "name": "Calendar",
    "description": "Soon.", "repo": "https://github.com/joshii-h/blueferry-plugin-calendar",
    "ref": None, "capabilities": ["calendar"], "icon": "view-calendar",
}


def _index(*plugins) -> bytes:
    return json.dumps({"version": 1, "plugins": list(plugins)}).encode()


def test_entries_are_validated_field_by_field() -> None:
    entry = parse_entry(IMMICH)
    assert entry is not None and entry.available and entry.capabilities == ("photos",)
    soon = parse_entry(SOON)
    assert soon is not None and not soon.available
    for change in (
        {"repo": "http://github.com/x/y"}, {"repo": "git@github.com:x/y"},
        {"repo": "ext::sh -c id"}, {"id": "nope"}, {"name": ""}, {"ref": "--upload-pack=x"},
        {"ref": "a..b"}, {"api_version": "1"}, {"capabilities": "photos"},
    ):
        assert parse_entry({**IMMICH, **change}) is None, change
    odd = parse_entry({**IMMICH, "name": "<b>Imm\x1bich</b>\n", "icon": "../evil",
                       "screenshot": "javascript:alert(1)", "min_blueferry": "x",
                       "capabilities": ["photos", "BAD CAP", 3]})
    assert odd is not None
    assert odd.name == "<b>Imm ich</b>" and odd.icon == "" and odd.screenshot == ""
    assert odd.min_blueferry == "" and odd.capabilities == ("photos",)


def test_index_documents() -> None:
    entries = parse_index(_index(IMMICH, SOON, IMMICH, {"id": "broken"}))
    assert [entry.id for entry in entries] == [IMMICH["id"], SOON["id"]]
    for data in (b"[]", b"nope", json.dumps({"version": 2, "plugins": []}).encode(),
                 b"x" * (MAX_INDEX_BYTES + 1)):
        with pytest.raises(IndexFetchError):
            parse_index(data)


@given(st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=30),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=10), children, max_size=6),
    max_leaves=30,
))
def test_parser_only_raises_its_own_error(document) -> None:
    for data in (json.dumps(document).encode(),
                 json.dumps({"version": 1, "plugins": [document]}).encode()):
        try:
            parse_index(data)
        except IndexFetchError:
            pass


class _Fetch:
    def __init__(self, data: bytes | Exception) -> None:
        self.data, self.calls = data, []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def test_cache_ttl_and_offline_fallback(tmp_path) -> None:
    now = [10_000.0]
    fetch = _Fetch(_index(IMMICH))
    index = PluginIndex(fetch=fetch, cache=tmp_path, clock=lambda: now[0], ttl=60)
    # mtime is real time; make the clock agree with it
    entries, note = index.load(DEFAULT_INDEX_URL)
    cached = next(tmp_path.iterdir())
    now[0] = cached.stat().st_mtime + 1
    assert note == "" and len(entries) == 1 and len(fetch.calls) == 1
    index.load(DEFAULT_INDEX_URL)
    assert len(fetch.calls) == 1  # fresh cache
    now[0] += 120
    fetch.data = IndexFetchError("could not fetch the index (URLError)")
    entries, note = index.load(DEFAULT_INDEX_URL)
    assert len(entries) == 1 and "cached" in note and len(fetch.calls) == 2
    with pytest.raises(IndexFetchError):
        PluginIndex(fetch=fetch, cache=tmp_path / "empty").load(DEFAULT_INDEX_URL)


def test_catalog_merges_indexes_and_marks_install_state(tmp_path) -> None:
    second = "https://example.org/more.json"
    fetches = {DEFAULT_INDEX_URL: _index(IMMICH, SOON),
               second: _index({**IMMICH, "name": "Shadowed"},
                              {**SOON, "id": "io.x.future", "api_version": 9, "ref": "v1"})}
    def fetch(url: str) -> bytes:
        if url not in fetches:
            raise IndexFetchError("could not fetch the index (URLError)")
        return fetches[url]

    index = PluginIndex(fetch=fetch, cache=tmp_path)
    record = InstallRecord(IMMICH["id"], IMMICH["repo"], "v0.1.0", "a" * 40, "/v")
    catalog = index.catalog(
        [DEFAULT_INDEX_URL, second, "https://broken.example/x"],
        installed={IMMICH["id"]: "0.1.0"}, records={IMMICH["id"]: record},
        blueferry_version="0.8.1", supported_api=frozenset({1}),
    )
    by_id = {item.entry.id: item for item in catalog.items}
    assert by_id[IMMICH["id"]].entry.name == "Immich photos"
    assert by_id[IMMICH["id"]].installed and by_id[IMMICH["id"]].update_available
    assert by_id[IMMICH["id"]].installed_ref == "v0.1.0"
    assert not by_id[SOON["id"]].entry.available
    assert not by_id["io.x.future"].compatible
    assert [url for url, _problem in catalog.problems] == ["https://broken.example/x"]
    assert [item.entry.id for item in search(catalog.items, "PHOTOS")] == [IMMICH["id"]]


def test_index_urls_from_settings() -> None:
    assert index_urls({}) == [DEFAULT_INDEX_URL]
    assert index_urls({"indexes": ["http://x", "https://a.example/i.json"]}) == [
        "https://a.example/i.json"]
    assert index_urls({"indexes": []}) == []
