"""Toolkit-free rows and forms for the plugin settings of every client.

The Qt settings page and the terminal client build the same lists from
these helpers: installed plugins with status, store cards from the plugin
indexes, the confirmation rows of an install, and settings forms from a
manifest's ``[Config …]`` schema. Functions marked *blocking* talk to
plugins, git or the network and belong on a worker thread. Everything
returned is plain text.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from blueferry import __version__
from blueferry.i18n import _
from blueferry.plugin_api import SUPPORTED_API_VERSIONS
from blueferry.plugin_api.client import PluginClient, PluginError, plain_text
from blueferry.plugin_api.config import SECRET_MASK
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.plugin_index import Catalog, PluginIndex, StoreItem, index_urls
from blueferry.plugin_manager import PluginEntry, PluginManager, PreparedInstall

CAPABILITY_LABELS = {
    "photos": _("Photos"),
    "calendar": _("Calendar"),
    "conversations": _("Conversations"),
    "files": _("Files"),
    "shortcuts": _("Shortcuts"),
    "share": _("Sharing"),
}
STATE_TEXT = {
    "ok": _("Ready"),
    "unconfigured": _("Needs setup"),
    "error": _("Error"),
    "busy": _("Busy"),
}
_TRUE = {"1", "true", "yes", "on"}


def capability_label(capability: str) -> str:
    return CAPABILITY_LABELS.get(capability, capability.replace("-", " ").capitalize())


def plugin_statuses(
    entries: Sequence[PluginEntry],
    client_factory: Callable[[PluginManifest], PluginClient] = PluginClient,
) -> dict[str, tuple[str, str]]:
    """*Blocking.* ``id -> (state, detail)`` for every enabled plugin."""
    result: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if not entry.enabled:
            result[entry.manifest.id] = ("disabled", "")
            continue
        try:
            status = client_factory(entry.manifest).status()
            result[entry.manifest.id] = (status.state, status.detail)
        except PluginError as error:
            result[entry.manifest.id] = ("error", plain_text(error, 200))
    return result


def entry_row(entry: PluginEntry, status: tuple[str, str] | None = None) -> dict[str, Any]:
    plugin = entry.manifest
    state, detail = status or ("unknown", "")
    if not entry.enabled:
        text = _("Disabled")
    else:
        text = STATE_TEXT.get(state, _("Checking…") if state == "unknown" else state)
    return {
        "id": plugin.id,
        "name": plugin.name,
        "version": plugin.version,
        "alias": plugin.alias,
        "capabilities": [capability_label(cap) for cap in plugin.capabilities],
        "source": entry.source,
        "ref": entry.record.ref_label if entry.record else "",
        "managed": entry.managed,
        "enabled": entry.enabled,
        "state": "disabled" if not entry.enabled else state,
        "stateText": text,
        "detail": detail,
        "hasConfig": bool(plugin.config),
    }


def store_row(item: StoreItem) -> dict[str, Any]:
    entry = item.entry
    if item.update_available:
        state, text = "update", _("Update available")
    elif item.installed:
        state, text = "installed", _("Installed ✓")
    elif not entry.available:
        state, text = "soon", _("Coming soon")
    elif not item.compatible:
        state, text = "incompatible", _("Needs a newer BlueFerry")
    else:
        state, text = "install", _("Install")
    return {
        "id": entry.id,
        "name": entry.name,
        "description": entry.description,
        "icon": entry.icon or "application-x-addon",
        "emoji": entry.emoji,
        "badges": [capability_label(cap) for cap in entry.capabilities],
        "repo": entry.repo,
        "ref": entry.ref,
        "installedRef": item.installed_ref,
        "screenshot": entry.screenshot,
        "state": state,
        "stateText": text,
        "installable": state in ("install", "update"),
    }


def summary_rows(prepared: PreparedInstall) -> list[dict[str, str]]:
    labels = {
        "Plugin": _("Plugin"), "Source": _("Source"), "Ref": _("Version"),
        "Capabilities": _("Capabilities"), "Runs": _("Runs"), "Settings": _("Settings"),
        "Replaces": _("Replaces"),
    }
    rows = [{"label": labels.get(label, label), "value": value}
            for label, value in prepared.summary()]
    if prepared.changes:
        rows.append({"label": _("Changes"), "value": "\n".join(prepared.changes)})
    return rows


def form_fields(manifest: PluginManifest, values: Mapping[str, object]) -> list[dict[str, Any]]:
    """One row per ``[Config …]`` field, with the current (masked) value."""
    rows = []
    for field in manifest.config:
        value = values.get(field.key, field.empty())
        rows.append({
            "key": field.key,
            "label": field.label,
            "type": field.type,
            "required": field.required,
            "help": field.help,
            "choices": list(field.choices),
            "minimum": field.minimum if field.minimum is not None else -2_147_483_648,
            "maximum": field.maximum if field.maximum is not None else 2_147_483_647,
            # A stored secret is never shown, only whether one exists.
            "value": "" if field.secret else value,
            "stored": field.secret and value == SECRET_MASK,
        })
    return rows


def parse_form(
    manifest: PluginManifest, raw: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, str]]:
    """Typed values from form input; empty secrets are left out (kept)."""
    values: dict[str, object] = {}
    errors: dict[str, str] = {}
    for field in manifest.config:
        if field.key not in raw:
            continue
        value = raw[field.key]
        if field.secret:
            if isinstance(value, str) and value.strip() and value != SECRET_MASK:
                values[field.key] = value.strip()
            continue
        if field.type == "bool":
            values[field.key] = (
                value if isinstance(value, bool) else str(value).strip().casefold() in _TRUE
            )
        elif field.type == "int":
            if isinstance(value, int) and not isinstance(value, bool):
                values[field.key] = value
            else:
                try:
                    values[field.key] = int(str(value).strip())
                except ValueError:
                    errors[field.key] = _("must be a whole number")
        else:
            values[field.key] = "" if value is None else str(value)
    return values, errors


def load_catalog(
    manager: PluginManager,
    index: PluginIndex,
    entries: Sequence[PluginEntry],
    *,
    refresh: bool = False,
) -> Catalog:
    """*Blocking.* The store cards for the configured indexes."""
    return index.catalog(
        index_urls(manager.settings()),
        installed={entry.manifest.id: entry.manifest.version for entry in entries},
        records=manager.records(),
        blueferry_version=__version__,
        supported_api=SUPPORTED_API_VERSIONS,
        refresh=refresh,
    )
