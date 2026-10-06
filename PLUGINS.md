# BlueFerry plugins

Plugins add features that are not about the iPhone link itself, for example a
photo gallery backed by a self-hosted service. The design is deliberately
small:

- A plugin is its **own process** with its own session-bus name. The daemon
  never loads plugin code and does not know about plugins.
- **Clients discover plugins themselves** by reading manifest files, then talk
  to the plugin over the user's session bus.
- The contract lives in `blueferry.plugin_api`, a self-contained package that
  imports nothing else from BlueFerry. Plugins import only from it. It ships
  inside BlueFerry and as its own distribution `blueferry-plugin-api`
  (`plugin-api/` in this repository; `src/blueferry/plugin_api` is a symlink
  to it).

Nothing in this document has been tested against real hardware; the plugin
path does not touch Bluetooth at all.

## Discovery

Clients read `*.plugin` files from, in this order:

1. `$XDG_DATA_HOME/blueferry/plugins/` (default `~/.local/share/blueferry/plugins/`)
2. each `$XDG_DATA_DIRS` entry + `/blueferry/plugins/` (default `/usr/local/share`, `/usr/share`)

The first manifest with a given `Id` wins, so a user manifest shadows a system
one. Discovery only parses files; it never starts anything. A manifest is
ignored, with a reason the clients can show (`blueferry plugins list`), when
it is malformed, larger than 16 KiB, not owned by the user or root, writable
by group or others, or written for an unsupported `ApiVersion`.

## Manifest

Desktop-entry syntax, group `[BlueFerry Plugin]`:

| Key | Required | Meaning |
| --- | --- | --- |
| `Id` | yes | Reverse-DNS id, e.g. `io.weirdware.blueferry.immich_photos`. |
| `Name` | yes | Display name (plain text, ≤ 80 characters). |
| `Version` | yes | The plugin's own version. |
| `ApiVersion` | yes | Plugin contract version it implements: `MAJOR` or `MAJOR.MINOR` (currently `1.1`). Clients ignore unknown major versions; the minor part is informational. |
| `MinBlueFerry` | yes | Oldest BlueFerry release the plugin works with. |
| `Capabilities` | yes | `;`-separated list. Known: `photos`; reserved: `conversations`. Unknown entries are dropped; a manifest with none left is ignored. |
| `Homepage`, `Source` | at least one | `https://` URLs. |
| `Exec` | yes | Command line that serves the plugin on the bus (used for its D-Bus service file). |
| `Cli` | no | Command line for the plugin's own CLI; `blueferry plugins <alias> …` forwards to it. |
| `Alias` | no | Short lowercase name for the CLI, e.g. `immich`. |

### Settings (`[Config <key>]`, ApiVersion 1.1)

Optional. Each group describes one setting; clients build a form from them in
file order. `<key>` is lowercase letters, digits and `_` (at most 32
characters, at most 24 settings).

| Key | Required | Meaning |
| --- | --- | --- |
| `Label` | yes | Form label (plain text, ≤ 80 characters). |
| `Type` | yes | `string`, `url` (https, http only for localhost), `secret`, `bool`, `int` or `choice`. |
| `Required` | no | `true` or `false` (default). |
| `Default` | no | Value of an unset field. Not allowed for `secret`. |
| `Help` | no | One line under the field (≤ 300 characters). |
| `Choices` | for `choice` | `;`-separated simple words. |
| `Min`, `Max` | no | Bounds for `int`. |

```
[Config url]
Label=Server URL
Type=url
Required=true

[Config api_key]
Label=API key
Type=secret
Required=true
```

A malformed settings group makes clients ignore the whole manifest, like any
other manifest error.

## Bus contract (ApiVersion 1)

- Bus name: `io.weirdware.BlueFerry.Plugin.<Id>` with `-` replaced by `_`.
- Object path: `/io/weirdware/BlueFerry/Plugin`.
- Activation: the plugin installs a D-Bus service file for its bus name
  (`$XDG_DATA_HOME/dbus-1/services/` or `/usr/share/dbus-1/services/`), so the
  first call starts it. It may exit when idle (the base class does after 10
  minutes) and is started again on the next call.

`io.weirdware.BlueFerry.Plugin1` (every plugin):

| Member | Signature | Meaning |
| --- | --- | --- |
| `GetInfo()` | `→ s` | JSON `{id, name, version, api_version, api_minor, capabilities}`. |
| `Status()` | `→ s` | JSON `{state, detail?, server?}`; `state` is `ok`, `unconfigured`, `error` or `busy`. |
| `GetConfig()` | `→ s` | Since 1.1, plugins with settings. JSON `{values: {key: value}}` for every manifest setting. A `secret` is `"********"` when stored and `""` when not; its value never leaves the plugin. |
| `SetConfig(s json)` | `→ s` | Since 1.1. A JSON object with the settings to change (at most 16 KiB). The plugin validates it against its schema and answers `{ok: true}` or `{ok: false, errors: {key: reason}}` (`""` for the whole form). A `secret` that is missing, empty or the mask keeps its stored value; unknown keys are errors. |

`io.weirdware.BlueFerry.Photos1` (capability `photos`):

| Member | Signature | Meaning |
| --- | --- | --- |
| `ListRecent(u limit)` | `→ s` | JSON array, newest first, at most 200: `{id, taken_at, type, thumbnail, original?}`. `type` is `image`, `video` or `other`; `thumbnail`/`original` are local paths or empty. |
| `FetchOriginal(s id)` | `→ s` | Download (or reuse) the original and return its local path. |
| `Changed()` | signal | Content-free: something changed, call `ListRecent` again. |

Errors are D-Bus errors under `io.weirdware.BlueFerry.Plugin.Error.*`
(`Failed`, `RateLimited`). Messages are short and contain no personal data.

### Versioning

- Additive changes (a new optional JSON key, a new method, a new capability
  interface such as `Conversations1`) keep `ApiVersion`. Clients ignore keys
  they do not know.
- Anything a v1 client could misread (renamed or retyped keys, changed
  semantics) bumps `ApiVersion`. A client lists the versions it supports in
  `plugin_api.SUPPORTED_API_VERSIONS` and ignores other manifests with a
  clear message instead of guessing.
- Capability interfaces carry their own number (`Photos1`), so one capability
  can evolve without touching the others.

## Security

- Clients use the **session bus** only and refuse a plugin whose bus name is
  owned by a different Unix user.
- Plugin data is **untrusted**: replies over 512 KiB are rejected, JSON is
  validated field by field, ids match `[A-Za-z0-9_-]{1,64}`, text is shown as
  plain text only, and a path is used only if it resolves (symlinks included)
  to a regular file owned by the user below `$XDG_CACHE_HOME/blueferry/` or
  `~/.cache/blueferry/` (a bus-activated plugin inherits the bus daemon's
  environment, which may lack the client's `XDG_CACHE_HOME`).
- Clients call plugins from worker threads with timeouts. A plugin that
  crashes, hangs or answers garbage produces an error message in the client,
  never a crash.
- The base service rate-limits each caller (120 calls/min) and runs network
  and disk work off its main loop. `run()` exits after the idle time only
  when no call is still in flight.
- Secrets (API keys) belong to the plugin: keep them in the Secret Service
  keyring, or in a 0600 file as a fallback, never in the manifest. Settings
  forms send a secret into the plugin only when the user typed a new one,
  and `GetConfig()` reports only whether one is stored (the base class masks
  it even if `config_values()` returns the plain value).

## Writing a plugin

```python
from blueferry.plugin_api.manifest import parse_manifest
from blueferry.plugin_api.service import PhotosService, run

class MyPhotos(PhotosService):
    def status(self):
        return {"state": "ok"}

    def list_recent(self, limit):        # worker thread
        return [{"id": "1", "taken_at": "2026-10-06T18:00:00Z",
                 "type": "image", "thumbnail": "/home/me/.cache/blueferry/my/1.webp"}]

    def fetch_original(self, photo_id):  # worker thread
        return "/home/me/.cache/blueferry/my/1.jpg"

def main():
    manifest = parse_manifest(MANIFEST_TEXT)
    return run(lambda bus: MyPhotos(manifest, bus))
```

A plugin with `[Config …]` groups also implements `config_values()` (the
stored settings; for a secret anything truthy) and `apply_config(values)`
(validated settings; raise `ConfigError(key, reason)` to reject one). Both
run on the worker thread.

Tests use `blueferry.plugin_api.testing`: `inline_service()` runs worker and
main-loop hand-offs inline, `ServiceTransport` lets a real `PluginClient`
call the service in-process through the full validation, and
`ScriptedTransport` replays canned replies.

### Plugin from its own repository

A plugin should be movable into its own Git repository unchanged. Layout
(the bundled `plugins/immich_photos/` follows it):

```
my-plugin/
├── pyproject.toml            # depends on blueferry (for blueferry.plugin_api)
├── README.md
├── data/
│   └── io.example.my_plugin.plugin
├── src/blueferry_my_plugin/
│   ├── __init__.py
│   ├── __main__.py           # "serve" and the setup CLI
│   └── service.py            # PhotosService subclass
└── tests/
```

Rules: import only `blueferry.plugin_api` from BlueFerry; declare a
`[project.scripts]` entry point that serves and sets up the plugin; ship the
manifest as package data and have the setup command install it together with
the D-Bus service file. Installation from a Git URL is not implemented yet;
the manifest keys `Version`, `ApiVersion`, `MinBlueFerry` and `Source` are
there so it can be added without changing the format.
