"""Contract between BlueFerry clients and out-of-process plugins.

Self-contained on purpose: this package imports nothing else from
``blueferry`` so it can later be split out as its own distribution. Plugins
import only from here. See PLUGINS.md for the interface contract.
"""
from __future__ import annotations

# Version of the Plugin1 contract (manifest keys, Plugin1 and the capability
# interfaces below). Additive changes keep the number; anything a v1 client
# could misread bumps it.
API_VERSION = 1
SUPPORTED_API_VERSIONS = frozenset({1})

BUS_NAME_PREFIX = "io.weirdware.BlueFerry.Plugin."
OBJECT_PATH = "/io/weirdware/BlueFerry/Plugin"
PLUGIN_INTERFACE = "io.weirdware.BlueFerry.Plugin1"
PHOTOS_INTERFACE = "io.weirdware.BlueFerry.Photos1"

CAPABILITY_PHOTOS = "photos"
KNOWN_CAPABILITIES = frozenset({CAPABILITY_PHOTOS, "conversations"})

# Everything a plugin returns is untrusted: clients reject larger replies.
MAX_REPLY_BYTES = 512 * 1024
MAX_RECENT_PHOTOS = 200

__all__ = [
    "API_VERSION",
    "BUS_NAME_PREFIX",
    "CAPABILITY_PHOTOS",
    "KNOWN_CAPABILITIES",
    "MAX_RECENT_PHOTOS",
    "MAX_REPLY_BYTES",
    "OBJECT_PATH",
    "PHOTOS_INTERFACE",
    "PLUGIN_INTERFACE",
    "SUPPORTED_API_VERSIONS",
]
