"""Contract between BlueFerry clients and out-of-process plugins.

Self-contained on purpose: this package imports nothing else from
``blueferry``; it also ships as the distribution ``blueferry-plugin-api``. Plugins
import only from here. See PLUGINS.md for the interface contract.
"""
from __future__ import annotations

# Version of the Plugin1 contract (manifest keys, Plugin1 and the capability
# interfaces below). Additive changes keep the number; anything a v1 client
# could misread bumps it.
API_VERSION = 1
# Compatible additions within API_VERSION: 1 added the optional settings
# schema (``[Config <key>]``) with Plugin1.GetConfig/SetConfig; 2 added the
# generic UI surfaces (capabilities ``card``, ``share`` and ``notify``). A
# manifest that needs a newer minor than this is ignored with a message.
API_MINOR = 2
SUPPORTED_API_VERSIONS = frozenset({1})

BUS_NAME_PREFIX = "io.weirdware.BlueFerry.Plugin."
OBJECT_PATH = "/io/weirdware/BlueFerry/Plugin"
PLUGIN_INTERFACE = "io.weirdware.BlueFerry.Plugin1"
PHOTOS_INTERFACE = "io.weirdware.BlueFerry.Photos1"
# ApiVersion 1.2 surfaces, one interface per capability like Photos1.
CARD_INTERFACE = "io.weirdware.BlueFerry.Card1"
SHARE_INTERFACE = "io.weirdware.BlueFerry.Share1"
NOTIFY_INTERFACE = "io.weirdware.BlueFerry.Notify1"

CAPABILITY_PHOTOS = "photos"
CAPABILITY_CARD = "card"
CAPABILITY_SHARE = "share"
CAPABILITY_NOTIFY = "notify"
KNOWN_CAPABILITIES = frozenset({
    CAPABILITY_PHOTOS, "conversations", CAPABILITY_CARD, CAPABILITY_SHARE, CAPABILITY_NOTIFY,
})

# Everything a plugin returns is untrusted: clients reject larger replies.
MAX_REPLY_BYTES = 512 * 1024
MAX_RECENT_PHOTOS = 200

__all__ = [
    "API_MINOR",
    "API_VERSION",
    "BUS_NAME_PREFIX",
    "CAPABILITY_CARD",
    "CAPABILITY_NOTIFY",
    "CAPABILITY_PHOTOS",
    "CAPABILITY_SHARE",
    "CARD_INTERFACE",
    "KNOWN_CAPABILITIES",
    "MAX_RECENT_PHOTOS",
    "MAX_REPLY_BYTES",
    "NOTIFY_INTERFACE",
    "OBJECT_PATH",
    "PHOTOS_INTERFACE",
    "PLUGIN_INTERFACE",
    "SHARE_INTERFACE",
    "SUPPORTED_API_VERSIONS",
]
