"""Plugin manifests: desktop-entry-like ``*.plugin`` files.

Example ``~/.local/share/blueferry/plugins/io.example.photos.plugin``::

    [BlueFerry Plugin]
    Id=io.example.photos
    Name=Example photos
    Version=1.0.0
    ApiVersion=1
    MinBlueFerry=0.8
    Capabilities=photos;
    Homepage=https://example.org/photos
    Source=https://git.example.org/photos.git
    Exec=blueferry-plugin-example serve
    Cli=blueferry-plugin-example
    Alias=example

Discovery never runs anything; it only parses files. A manifest that is
malformed, too large or written for an unsupported ``ApiVersion`` is ignored
with a reason the clients can show.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    BUS_NAME_PREFIX,
    KNOWN_CAPABILITIES,
    SUPPORTED_API_VERSIONS,
)

GROUP = "BlueFerry Plugin"
SUFFIX = ".plugin"
MAX_MANIFEST_BYTES = 16 * 1024
MAX_MANIFESTS_PER_DIRECTORY = 64
# Reverse-DNS: at least two dot-separated elements of [A-Za-z0-9_-], none
# starting with a digit, so the derived bus name is valid.
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)+$")
_ALIAS = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_VERSION = re.compile(r"^\d+(\.\d+){0,3}([-+.~][0-9A-Za-z.]+)?$")
_URL = re.compile(r"^https://[^\s/]+(/\S*)?$")
_REQUIRED = (
    "Id", "Name", "Version", "ApiVersion", "MinBlueFerry", "Capabilities", "Exec",
)


class ManifestError(ValueError):
    """A manifest that clients must ignore; the message says why."""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    name: str
    version: str
    api_version: int
    min_blueferry: str
    capabilities: tuple[str, ...]
    exec: tuple[str, ...]
    homepage: str = ""
    source: str = ""
    cli: tuple[str, ...] = ()
    alias: str = ""
    path: Path | None = None

    @property
    def bus_name(self) -> str:
        return bus_name_for(self.id)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class Discovery:
    plugins: tuple[PluginManifest, ...] = ()
    # (file name, reason) for every manifest that was skipped.
    ignored: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def with_capability(self, capability: str) -> tuple[PluginManifest, ...]:
        return tuple(plugin for plugin in self.plugins if plugin.has(capability))

    def find(self, name: str) -> PluginManifest | None:
        for plugin in self.plugins:
            if name in (plugin.id, plugin.alias):
                return plugin
        return None


def bus_name_for(plugin_id: str) -> str:
    """``io.weirdware.BlueFerry.Plugin.<id>``; '-' is not valid in names."""
    return BUS_NAME_PREFIX + plugin_id.replace("-", "_")


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+){0,3})", value.strip())
    if match is None:
        raise ManifestError(f"not a version: {value!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def _keyfile(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    group = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            continue
        if group != GROUP:
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or "[" in key:
            # Localized keys (Name[de]) are allowed and ignored for now.
            if "[" in key:
                continue
            raise ManifestError(f"malformed line: {raw[:40]!r}")
        if key in values:
            raise ManifestError(f"duplicate key {key}")
        values[key] = value.strip()
    if group is None and not values:
        raise ManifestError(f"missing [{GROUP}] group")
    return values


def _argv(value: str, key: str) -> tuple[str, ...]:
    try:
        argv = tuple(shlex.split(value))
    except ValueError as error:
        raise ManifestError(f"{key} is not a valid command line") from error
    if not argv:
        raise ManifestError(f"{key} is empty")
    if any("\x00" in part or "\n" in part for part in argv):
        raise ManifestError(f"{key} contains control characters")
    return argv


def parse_manifest(
    text: str,
    *,
    path: Path | None = None,
    blueferry_version: str | None = None,
) -> PluginManifest:
    """Parse and validate one manifest; raise ManifestError to skip it."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest is too large")
    values = _keyfile(text)
    missing = [key for key in _REQUIRED if not values.get(key)]
    if missing:
        raise ManifestError("missing " + ", ".join(missing))
    plugin_id = values["Id"]
    if len(plugin_id) > 120 or not _ID.fullmatch(plugin_id):
        raise ManifestError("Id must be a reverse-DNS name like io.example.photos")
    try:
        api_version = int(values["ApiVersion"])
    except ValueError:
        raise ManifestError("ApiVersion must be an integer") from None
    if api_version not in SUPPORTED_API_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_API_VERSIONS))
        raise ManifestError(
            f"written for plugin API {api_version}; this BlueFerry supports {supported}"
        )
    for key in ("Version", "MinBlueFerry"):
        if len(values[key]) > 40 or not _VERSION.fullmatch(values[key]):
            raise ManifestError(f"{key} is not a version")
    if blueferry_version is not None and version_tuple(blueferry_version) < version_tuple(
        values["MinBlueFerry"]
    ):
        raise ManifestError(f"needs BlueFerry {values['MinBlueFerry']} or newer")
    capabilities = tuple(
        dict.fromkeys(part.strip() for part in values["Capabilities"].split(";") if part.strip())
    )
    usable = tuple(c for c in capabilities if c in KNOWN_CAPABILITIES)
    if not usable:
        raise ManifestError("no capability this BlueFerry understands")
    for key in ("Homepage", "Source"):
        if values.get(key) and (len(values[key]) > 300 or not _URL.fullmatch(values[key])):
            raise ManifestError(f"{key} must be an https URL")
    if not (values.get("Homepage") or values.get("Source")):
        raise ManifestError("missing Homepage or Source")
    alias = values.get("Alias", "")
    if alias and not _ALIAS.fullmatch(alias):
        raise ManifestError("Alias must be a short lowercase word")
    name = values["Name"]
    if len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise ManifestError("Name is too long or contains control characters")
    return PluginManifest(
        id=plugin_id,
        name=name,
        version=values["Version"],
        api_version=api_version,
        min_blueferry=values["MinBlueFerry"],
        capabilities=usable,
        exec=_argv(values["Exec"], "Exec"),
        homepage=values.get("Homepage", ""),
        source=values.get("Source", ""),
        cli=_argv(values["Cli"], "Cli") if values.get("Cli") else (),
        alias=alias,
        path=path,
    )


def default_directories() -> tuple[Path, ...]:
    """User manifests first: they shadow system ones with the same Id."""
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [data_home, *[part for part in data_dirs.split(":") if part]]
    return tuple(dict.fromkeys(Path(root) / "blueferry" / "plugins" for root in roots))


def _read_manifest(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        current = os.fstat(stream.fileno())
        if not stat.S_ISREG(current.st_mode):
            raise ManifestError("not a regular file")
        # Another user's writable file could point clients at any program.
        if current.st_uid not in (0, os.getuid()) or current.st_mode & 0o022:
            raise ManifestError("owned or writable by another user")
        data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest is too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("manifest is not UTF-8") from None


def discover(
    directories: Sequence[Path] | None = None,
    *,
    blueferry_version: str | None = None,
) -> Discovery:
    """Parse every ``*.plugin`` file; the first manifest per Id wins."""
    plugins: dict[str, PluginManifest] = {}
    ignored: list[tuple[str, str]] = []
    for directory in directories if directories is not None else default_directories():
        for path in _manifest_files(directory):
            try:
                manifest = parse_manifest(
                    _read_manifest(path), path=path, blueferry_version=blueferry_version,
                )
            except (ManifestError, OSError) as error:
                ignored.append((path.name, str(error) or type(error).__name__))
                continue
            plugins.setdefault(manifest.id, manifest)
    return Discovery(tuple(plugins.values()), tuple(ignored))


def _manifest_files(directory: Path) -> Iterable[Path]:
    try:
        names = sorted(
            entry.name for entry in os.scandir(directory)
            if entry.name.endswith(SUFFIX) and not entry.name.startswith(".")
        )
    except OSError:
        return ()
    return [directory / name for name in names[:MAX_MANIFESTS_PER_DIRECTORY]]
