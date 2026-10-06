"""Plugin settings: the manifest's ``[Config <key>]`` groups and their values.

A plugin describes its settings in the manifest; clients build a form from
that schema, read the current values with ``GetConfig()`` and write changes
with ``SetConfig()``. Validation runs in the plugin (:func:`validate`) and,
for a quick answer, in the client too. Secrets only travel *into* the
plugin: ``GetConfig()`` reports :data:`SECRET_MASK` for a stored secret and
an empty string for a missing one, and a client sends a secret only when the
user typed a new one.

Example manifest groups::

    [Config url]
    Label=Server URL
    Type=url
    Required=true
    Help=For example https://photos.example.org

    [Config api_key]
    Label=API key
    Type=secret
    Required=true
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

GROUP_PREFIX = "Config "
TYPES = frozenset({"string", "url", "secret", "bool", "int", "choice"})
SECRET_MASK = "********"
MAX_FIELDS = 24
MAX_CONFIG_BYTES = 16 * 1024
MAX_TEXT = 1024
MAX_URL = 2048
MAX_SECRET = 4096
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CHOICE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_URL = re.compile(r"^https://[^\s/?#]+(/\S*)?$")
_LOCAL_URL = re.compile(r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?(/\S*)?$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """One setting is invalid; ``field`` names it (empty: the whole form)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    label: str
    type: str
    required: bool = False
    default: object = None
    help: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    @property
    def secret(self) -> bool:
        return self.type == "secret"

    def empty(self) -> object:
        """The value of an unset field: its default, else a neutral value."""
        if self.default is not None:
            return self.default
        if self.type == "bool":
            return False
        if self.type == "int":
            return self.minimum if self.minimum is not None else 0
        return ""

    def coerce(self, value: object) -> object:
        """A valid value of this field's type; raise ConfigError otherwise."""
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            raise ConfigError(self.key, "must be on or off")
        if self.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(self.key, "must be a whole number")
            if self.minimum is not None and value < self.minimum:
                raise ConfigError(self.key, f"must be at least {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ConfigError(self.key, f"must be at most {self.maximum}")
            return value
        if not isinstance(value, str):
            raise ConfigError(self.key, "must be text")
        if any(not ch.isprintable() for ch in value):
            raise ConfigError(self.key, "contains control characters")
        text = value.strip()
        if self.type == "choice":
            if text not in self.choices:
                raise ConfigError(self.key, "is not one of the offered choices")
            return text
        if self.type == "url":
            if len(text) > MAX_URL or not (_URL.fullmatch(text) or _LOCAL_URL.fullmatch(text)):
                raise ConfigError(
                    self.key, "must be an https:// URL (http only for localhost)",
                )
            return text.rstrip("/")
        limit = MAX_SECRET if self.secret else MAX_TEXT
        if len(text) > limit:
            raise ConfigError(self.key, "is too long")
        return text


def _flag(value: str, key: str) -> bool:
    lowered = value.strip().casefold()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"Config {key}: Required must be true or false")


def _plain(value: str, limit: int, what: str) -> str:
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{what} is too long or contains control characters")
    return value


def parse_fields(groups: Sequence[tuple[str, Mapping[str, str]]]) -> tuple[ConfigField, ...]:
    """Fields from ``(key, group values)`` pairs in manifest order.

    Raises ValueError with a reason; the manifest parser turns that into a
    ManifestError so clients ignore the whole manifest.
    """
    if len(groups) > MAX_FIELDS:
        raise ValueError(f"more than {MAX_FIELDS} config fields")
    fields: list[ConfigField] = []
    seen: set[str] = set()
    for key, values in groups:
        if not _KEY.fullmatch(key):
            raise ValueError(f"config key {key[:40]!r} must be lowercase letters, digits or _")
        if key in seen:
            raise ValueError(f"duplicate config key {key}")
        seen.add(key)
        kind = values.get("Type", "")
        if kind not in TYPES:
            raise ValueError(f"Config {key}: unknown Type {kind[:20]!r}")
        label = _plain(values.get("Label", ""), 80, f"Config {key}: Label")
        if not label:
            raise ValueError(f"Config {key}: missing Label")
        choices: tuple[str, ...] = ()
        if kind == "choice":
            choices = tuple(dict.fromkeys(
                part.strip() for part in values.get("Choices", "").split(";") if part.strip()
            ))
            if not choices or len(choices) > 32 or not all(_CHOICE.fullmatch(c) for c in choices):
                raise ValueError(f"Config {key}: Choices must list simple words")
        minimum = maximum = None
        if kind == "int":
            try:
                minimum = int(values["Min"]) if values.get("Min") else None
                maximum = int(values["Max"]) if values.get("Max") else None
            except ValueError:
                raise ValueError(f"Config {key}: Min and Max must be integers") from None
        field = ConfigField(
            key=key, label=label, type=kind,
            required=_flag(values.get("Required", "false"), key),
            help=_plain(values.get("Help", ""), 300, f"Config {key}: Help"),
            choices=choices, minimum=minimum, maximum=maximum,
        )
        if values.get("Default") not in (None, ""):
            if kind == "secret":
                raise ValueError(f"Config {key}: a secret cannot have a Default")
            raw = values["Default"]
            try:
                parsed: object = (
                    _flag(raw, key) if kind == "bool" else int(raw) if kind == "int" else raw
                )
                field = _with_default(field, field.coerce(parsed))
            except (ValueError, ConfigError):
                raise ValueError(f"Config {key}: invalid Default") from None
        fields.append(field)
    return tuple(fields)


def _with_default(field: ConfigField, default: object) -> ConfigField:
    return ConfigField(
        key=field.key, label=field.label, type=field.type, required=field.required,
        default=default, help=field.help, choices=field.choices,
        minimum=field.minimum, maximum=field.maximum,
    )


def masked(fields: Sequence[ConfigField], values: Mapping[str, object]) -> dict[str, object]:
    """What ``GetConfig()`` may reveal: every field, secrets as a mask only."""
    shown: dict[str, object] = {}
    for field in fields:
        value = values.get(field.key)
        if field.secret:
            shown[field.key] = SECRET_MASK if value else ""
            continue
        try:
            shown[field.key] = field.empty() if value is None else field.coerce(value)
        except ConfigError:
            shown[field.key] = field.empty()
    return shown


def parse_update(text: str) -> dict[str, object]:
    """A ``SetConfig()`` argument: a JSON object of at most 16 KiB."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_CONFIG_BYTES:
        raise ConfigError("", "the settings are too large")
    try:
        value = json.loads(text)
    except ValueError:
        raise ConfigError("", "the settings are not valid JSON") from None
    if not isinstance(value, dict):
        raise ConfigError("", "the settings must be a JSON object")
    return value


def validate(
    fields: Sequence[ConfigField],
    current: Mapping[str, object],
    update: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, str]]:
    """Merge ``update`` into ``current`` and check every field.

    Returns ``(values, errors)``. ``values`` holds every non-secret field and
    only the secrets that ``update`` sets anew; a secret that is empty or
    equal to :data:`SECRET_MASK` keeps its stored value. Unknown keys are
    errors, so a typo never silently does nothing.
    """
    by_key = {field.key: field for field in fields}
    errors: dict[str, str] = {
        str(key)[:32]: "is not a setting of this plugin" for key in update if key not in by_key
    }
    values: dict[str, object] = {}
    for field in fields:
        if field.secret:
            new = update.get(field.key)
            if new in (None, "", SECRET_MASK):
                if field.required and not current.get(field.key):
                    errors[field.key] = "is required"
                continue
        else:
            new = update.get(field.key, current.get(field.key))
            if new is None or new == "":
                if field.required:
                    errors[field.key] = "is required"
                values[field.key] = field.empty()
                continue
        try:
            values[field.key] = field.coerce(new)
        except ConfigError as error:
            errors[field.key] = error.message
    return values, errors
