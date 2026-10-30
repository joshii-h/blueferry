"""One search over conversations, messages, contacts and call history.

Local history is encrypted at rest, so there is no search index on disk
(no SQLite FTS, no plaintext token table). The daemon searches the data it
already holds decrypted in memory for its clients: the projected
conversation list (the most recent messages plus starred conversations),
the contact cache, and the retained call history. Nothing is precomputed
while nobody searches; every group is cut to a caller-chosen limit.

Matching is a case-insensitive substring test after collapsing
whitespace. Phone numbers additionally match by their digits once the
query has at least three digits and nothing but phone punctuation.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from blueferry.call_history import CallRecord, resolve_contact_name
from blueferry.limits import MAX_SEARCH_SNIPPET_CHARS
from blueferry.threads import _timestamp_order

_PHONE_QUERY = re.compile(r"[\d\s+()./-]+")
_MIN_DIGIT_QUERY = 3


def normalize_query(query: str) -> str:
    return " ".join(str(query).split())


def _digit_needle(needle: str) -> str:
    digits = re.sub(r"\D", "", needle)
    if len(digits) >= _MIN_DIGIT_QUERY and _PHONE_QUERY.fullmatch(needle):
        return digits
    return ""


def snippet(text: str, start: int, length: int) -> tuple[str, int, int]:
    """A one-line excerpt around a match: ``(text, match start, match length)``.

    ``text`` is already whitespace-collapsed. The excerpt is at most
    MAX_SEARCH_SNIPPET_CHARS long (plus ellipses) and keeps some context
    before the match; the offsets refer to the excerpt and are clamped to
    it (case folding can change a match's length, e.g. for "ß").
    """
    start = max(0, min(start, len(text)))
    before = min(start, MAX_SEARCH_SNIPPET_CHARS // 4)
    begin = start - before
    end = min(len(text), begin + MAX_SEARCH_SNIPPET_CHARS)
    excerpt = text[begin:end]
    offset = before
    if begin > 0:
        excerpt, offset = "…" + excerpt, offset + 1
    if end < len(text):
        excerpt += "…"
    return excerpt, offset, max(0, min(length, len(excerpt) - offset))


def search_threads(
    threads: Sequence[dict], needle: str, limit: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(conversations group, messages group)`` for one query.

    Conversations match by name or member; messages by body text, newest
    first. Each message hit carries the thread key and the message handle a
    client needs to open it.
    """
    needle = normalize_query(needle)
    folded = needle.casefold()
    conversations: list[dict[str, Any]] = []
    conversation_total = 0
    hits: list[tuple[float, dict[str, Any]]] = []
    message_total = 0
    for thread in threads:
        name = str(thread.get("name") or "")
        members = [str(value) for value in thread.get("members") or ()]
        if any(folded in value.casefold() for value in (name, *members)):
            conversation_total += 1
            if len(conversations) < limit:
                conversations.append({
                    "thread_key": str(thread.get("key") or ""),
                    "name": name,
                    "is_group": bool(thread.get("is_group")),
                    "timestamp": str(thread.get("last_ts") or ""),
                })
        for message in thread.get("messages") or ():
            body = " ".join(str(message.get("body") or "").split())
            position = body.casefold().find(folded)
            if position < 0:
                continue
            message_total += 1
            hits.append((_timestamp_order(str(message.get("timestamp") or "")), {
                "thread": thread, "message": message, "body": body, "position": position,
            }))
    # Newest first, comparing instants (local offsets and UTC may differ).
    hits.sort(key=lambda item: item[0], reverse=True)
    messages = []
    for _order, hit in hits[:limit]:
        thread, message = hit["thread"], hit["message"]
        text, start, length = snippet(hit["body"], hit["position"], len(needle))
        messages.append({
            "thread_key": str(thread.get("key") or ""),
            "handle": str(message.get("handle") or ""),
            "thread_name": str(thread.get("name") or ""),
            "is_group": bool(thread.get("is_group")),
            "sender": str(message.get("sender") or ""),
            "outgoing": bool(message.get("outgoing")),
            "timestamp": str(message.get("timestamp") or ""),
            "snippet": text,
            "match_start": start,
            "match_length": length,
        })
    return (
        {"total": conversation_total, "results": conversations},
        {"total": message_total, "results": messages},
    )


def search_calls(
    records: Iterable[CallRecord],
    needle: str,
    limit: int,
    resolve: Callable[[str | None], str | None] | None,
) -> dict[str, Any]:
    """Call-history hits by caller name or number, newest first (as stored)."""
    needle = normalize_query(needle)
    folded = needle.casefold()
    digits = _digit_needle(needle)
    results: list[dict[str, Any]] = []
    total = 0
    for record in records:
        resolved = resolve_contact_name(record, resolve) if resolve is not None else None
        name = resolved or record.name or ""
        number = re.sub(r"\D", "", record.address or record.phone or "")
        if not ((name and folded in name.casefold()) or (digits and digits in number)):
            continue
        total += 1
        if len(results) < limit:
            results.append({
                "id": record.key,
                "direction": record.direction,
                "timestamp": record.occurred_at.isoformat(),
                "address": record.address,
                "name": name,
                "contact_name": resolved,
            })
    return {"total": total, "results": results}
