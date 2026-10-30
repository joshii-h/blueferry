"""Contact-card details from PBAP vCards: labelled numbers, mail, addresses.

:mod:`blueferry.contacts` keeps the messaging view of a card (name plus the
normalized phone and email identities used for routing). This module keeps
what a contact *view* needs on top of that: every number as the iPhone
stores it with its label, labelled email addresses, postal addresses, the
organization and the job title.

The details are stored inside the sealed contact payload (purpose
``contact-record-v1``) and never in a plaintext table. Older versions ignore
the extra key, so a downgrade keeps working and drops the details at the
next sync.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from blueferry import config
from blueferry.events import is_email_shaped
from blueferry.limits import (
    MAX_CONTACT_ADDRESS_CHARS,
    MAX_CONTACT_ADDRESSES_PER_CARD,
    MAX_CONTACT_DETAIL_CHARS,
    MAX_CONTACT_LABEL_CHARS,
    MAX_CONTACT_POSTAL_ADDRESSES_PER_CARD,
)
from blueferry.phone_numbers import contact_phone

# Machine-readable label kinds. Clients translate them; ``custom`` carries
# the user's own label text in ``label``.
PHONE_KINDS = frozenset({
    "mobile", "iphone", "home", "work", "main", "home_fax", "work_fax",
    "other_fax", "pager", "other", "custom",
})
EMAIL_KINDS = frozenset({"home", "work", "other", "custom"})
ADDRESS_KINDS = frozenset({"home", "work", "other", "custom"})
POSTAL_FIELDS = (
    "po_box", "extended", "street", "locality", "region", "postal_code", "country",
)

# Apple's built-in X-ABLabel values (``_$!<Mobile>!$_``) for grouped items.
_APPLE_LABELS = {
    "MOBILE": "mobile", "HOME": "home", "WORK": "work", "MAIN": "main",
    "HOMEFAX": "home_fax", "WORKFAX": "work_fax", "OTHERFAX": "other_fax",
    "PAGER": "pager", "OTHER": "other", "IPHONE": "iphone",
}


@dataclass(frozen=True, slots=True)
class LabeledValue:
    """One number or email address with its label.

    ``number`` is the normalized phone spelling messaging and dialling use
    (empty for email and for numbers that cannot be normalized).
    """

    kind: str
    label: str
    value: str
    number: str = ""


@dataclass(frozen=True, slots=True)
class PostalAddress:
    kind: str
    label: str
    po_box: str = ""
    extended: str = ""
    street: str = ""
    locality: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""

    def fields(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in POSTAL_FIELDS}


@dataclass(frozen=True, slots=True)
class ContactDetails:
    phones: tuple[LabeledValue, ...] = ()
    emails: tuple[LabeledValue, ...] = ()
    addresses: tuple[PostalAddress, ...] = ()
    organization: str = ""
    title: str = ""

    def __bool__(self) -> bool:
        return bool(
            self.phones or self.emails or self.addresses
            or self.organization or self.title
        )

    def to_storage(self) -> dict[str, Any]:
        """Compact JSON-ready form for the sealed contact payload."""
        value: dict[str, Any] = {}
        if self.phones:
            value["phones"] = [
                [item.kind, item.label, item.value, item.number] for item in self.phones
            ]
        if self.emails:
            value["emails"] = [[item.kind, item.label, item.value] for item in self.emails]
        if self.addresses:
            value["addresses"] = [
                [item.kind, item.label, *(getattr(item, name) for name in POSTAL_FIELDS)]
                for item in self.addresses
            ]
        if self.organization:
            value["organization"] = self.organization
        if self.title:
            value["title"] = self.title
        return value

    @classmethod
    def from_storage(cls, value: object) -> ContactDetails:
        """Rebuild from decrypted JSON; malformed parts are dropped, not raised."""
        if not isinstance(value, Mapping):
            return EMPTY
        phones = []
        for item in _rows(value.get("phones"), MAX_CONTACT_ADDRESSES_PER_CARD):
            if len(item) != 4:
                continue
            kind, label, text, number = item
            if kind not in PHONE_KINDS or not text or len(text) > MAX_CONTACT_ADDRESS_CHARS:
                continue
            phones.append(LabeledValue(
                kind, label[:MAX_CONTACT_LABEL_CHARS], text,
                number if len(number) <= MAX_CONTACT_ADDRESS_CHARS else "",
            ))
        emails = []
        for item in _rows(value.get("emails"), MAX_CONTACT_ADDRESSES_PER_CARD):
            if len(item) != 3:
                continue
            kind, label, text = item
            if kind not in EMAIL_KINDS or len(text) > MAX_CONTACT_ADDRESS_CHARS or not (
                is_email_shaped(text)
            ):
                continue
            emails.append(LabeledValue(kind, label[:MAX_CONTACT_LABEL_CHARS], text))
        addresses = []
        for item in _rows(value.get("addresses"), MAX_CONTACT_POSTAL_ADDRESSES_PER_CARD):
            if len(item) != 2 + len(POSTAL_FIELDS) or item[0] not in ADDRESS_KINDS:
                continue
            parts = [part[:MAX_CONTACT_DETAIL_CHARS] for part in item[2:]]
            if any(parts):
                addresses.append(PostalAddress(
                    item[0], item[1][:MAX_CONTACT_LABEL_CHARS], *parts,
                ))
        return cls(
            tuple(phones), tuple(emails), tuple(addresses),
            _text(value.get("organization")), _text(value.get("title")),
        )


EMPTY = ContactDetails()


def _text(value: object) -> str:
    return value[:MAX_CONTACT_DETAIL_CHARS] if isinstance(value, str) else ""


def _rows(value: object, maximum: int) -> list[list[str]]:
    """Stored rows of strings only; anything else is skipped."""
    if not isinstance(value, list):
        return []
    rows = []
    for item in value[:maximum]:
        if isinstance(item, list) and all(isinstance(part, str) for part in item):
            rows.append(item)
    return rows


# ---- vCard parsing ----------------------------------------------------------

def _value_separator(line: str) -> int:
    """Index of the first ``:`` outside double-quoted parameter values."""
    quoted = False
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            return index
    return -1


def _split_escaped(value: str, separator: str = ";") -> list[str]:
    """Split at unescaped separators and resolve RFC 2426 text escapes."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append("\n" if character in "nN" else character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == separator:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return parts


def _unescape(value: str) -> str:
    return _split_escaped(value, separator="\0")[0]


def _property(line: str) -> tuple[str, str, frozenset[str], str] | None:
    """``(group, NAME, TYPE tokens, value)`` of one unfolded content line."""
    separator = _value_separator(line)
    if separator < 0:
        return None
    head, value = line[:separator], line[separator + 1:]
    name, *params = head.split(";")
    group, _, name = name.strip().rpartition(".")
    tokens: set[str] = set()
    for param in params:
        key, has_value, values = param.strip().partition("=")
        if has_value and key.strip().upper() != "TYPE":
            continue
        for token in (values if has_value else key).split(","):
            token = token.strip().strip('"').upper()
            if token:
                tokens.add(token)
    return group.upper(), name.upper(), frozenset(tokens), value


def _apple_label(value: str) -> tuple[str, str]:
    """``(kind, label)`` for an X-ABLabel value."""
    text = _unescape(value).strip()
    if text.startswith("_$!<") and text.endswith(">!$_"):
        kind = _APPLE_LABELS.get(text[4:-4].upper())
        if kind is not None:
            return kind, ""
        text = text[4:-4]
    return "custom", text[:MAX_CONTACT_LABEL_CHARS]


def _phone_kind(tokens: frozenset[str]) -> str:
    if "FAX" in tokens:
        if "HOME" in tokens:
            return "home_fax"
        return "work_fax" if "WORK" in tokens else "other_fax"
    for token, kind in (
        ("PAGER", "pager"), ("IPHONE", "iphone"), ("CELL", "mobile"),
        ("MAIN", "main"), ("HOME", "home"), ("WORK", "work"),
    ):
        if token in tokens:
            return kind
    return "other"


def _place_kind(tokens: frozenset[str]) -> str:
    if "HOME" in tokens:
        return "home"
    return "work" if "WORK" in tokens else "other"


def _labelled(
    kind: str, group: str, labels: Mapping[str, tuple[str, str]], allowed: Iterable[str],
) -> tuple[str, str]:
    """An X-ABLabel on the same group overrides the TYPE parameters."""
    if group and group in labels:
        label_kind, label = labels[group]
        if label_kind in allowed:
            return label_kind, label
        return "custom", label
    return kind, ""


def parse_card_details(body: str) -> ContactDetails:
    """The details of one unfolded vCard body (see ``iter_vcard_bodies``)."""
    lines = []
    labels: dict[str, tuple[str, str]] = {}
    for raw in body.splitlines():
        parsed = _property(raw.strip())
        if parsed is None:
            continue
        if parsed[1] == "X-ABLABEL" and parsed[0]:
            labels[parsed[0]] = _apple_label(parsed[3])
        else:
            lines.append(parsed)
    phones: list[LabeledValue] = []
    emails: list[LabeledValue] = []
    addresses: list[PostalAddress] = []
    organization = title = ""
    for group, name, tokens, value in lines:
        if name == "TEL" and len(phones) < MAX_CONTACT_ADDRESSES_PER_CARD:
            text = value.strip()
            if not text or len(text) > MAX_CONTACT_ADDRESS_CHARS:
                continue
            kind, label = _labelled(_phone_kind(tokens), group, labels, PHONE_KINDS)
            number = contact_phone(text, config.PHONE_REGION) or ""
            phones.append(LabeledValue(kind, label, text, number))
        elif name == "EMAIL" and len(emails) < MAX_CONTACT_ADDRESSES_PER_CARD:
            text = value.strip()
            if len(text) > MAX_CONTACT_ADDRESS_CHARS or not is_email_shaped(text):
                continue
            kind, label = _labelled(_place_kind(tokens), group, labels, EMAIL_KINDS)
            emails.append(LabeledValue(kind, label, text))
        elif name == "ADR" and len(addresses) < MAX_CONTACT_POSTAL_ADDRESSES_PER_CARD:
            parts = [part.strip()[:MAX_CONTACT_DETAIL_CHARS] for part in _split_escaped(value)]
            parts = (parts + [""] * len(POSTAL_FIELDS))[:len(POSTAL_FIELDS)]
            if not any(parts):
                continue
            kind, label = _labelled(_place_kind(tokens), group, labels, ADDRESS_KINDS)
            addresses.append(PostalAddress(kind, label, *parts))
        elif name == "ORG" and not organization:
            organization = ", ".join(
                part.strip() for part in _split_escaped(value) if part.strip()
            )[:MAX_CONTACT_DETAIL_CHARS]
        elif name == "TITLE" and not title:
            title = _unescape(value).strip()[:MAX_CONTACT_DETAIL_CHARS]
    return ContactDetails(
        tuple(phones), tuple(emails), tuple(addresses), organization, title,
    )

