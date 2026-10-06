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
| `ApiVersion` | yes | Plugin contract version it implements: `MAJOR` or `MAJOR.MINOR` (currently `1.2`). Clients ignore unknown major versions, and a manifest that needs a newer minor than the running BlueFerry knows (`1.3` on a 1.2 BlueFerry) is ignored with a message. |
| `MinBlueFerry` | yes | Oldest BlueFerry release the plugin works with. |
| `Capabilities` | yes | `;`-separated list. Known: `photos`, `card`, `share`, `notify` (1.2); reserved: `conversations`. Unknown entries are dropped; a manifest with none left is ignored. |
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

### Generic UI surfaces (ApiVersion 1.2)

Plugins never ship UI code. With these capabilities they describe what to
show, and BlueFerry renders it in the Qt client, the terminal client, the
tray and the CLI. All members live on **`Plugin1`** at the plugin's object
path; the manifest's capabilities decide which ones BlueFerry calls.
`blueferry.plugin_api` exports the names (`SURFACES_INTERFACE`,
`METHOD_GET_CARD_ITEMS`, `METHOD_INVOKE_ACTION`, `METHOD_SHARE_TARGETS`,
`METHOD_SEND_FILES`, `SIGNAL_CARD_CHANGED`, `SIGNAL_NOTIFY`).

Every string is untrusted and shown as one line of plain text, cut to
title 80, subtitle 160 and label 40 characters. Ids match
`[A-Za-z0-9_.-]{1,64}` (no `:`; clients write `PLUGIN:ITEM:ACTION`), icons
are freedesktop icon names (no paths).

| Member | Capability | Signature | Meaning |
| --- | --- | --- | --- |
| `GetCardItems()` | `card` | `→ s` | JSON `{"items": [{"id", "icon", "title", "subtitle"?, "actions": [{"id", "label", "icon"?, "kind": "button"\|"primary"}]}]}`; at most 8 items with 3 actions each, more are dropped. |
| `CardChanged()` | `card` | signal | Content-free: BlueFerry calls `GetCardItems` again (coalesced). |
| `InvokeAction(s item_id, s action_id, s args_json)` | `card`, `notify` | `→ s` | JSON `{"ok", "message"?, "open_uri"?}`. `args_json` is a JSON object (at most 4 KiB, `{}` today). For a popup button `item_id` is `"notify"`. |
| `ShareTargets()` | `share` | `→ s` | JSON `{"targets": [{"id", "label", "icon"}]}`, at most 16. |
| `SendFiles(s target_id, as paths)` | `share` | `→ s` | JSON `{"ok", "message"?, "job"?}`. Absolute paths of existing regular files, 1 to 64. Return quickly; report a long transfer as a card item (progress in the subtitle) and `CardChanged()`. |
| `Notify(s title, s body, s icon, s action_label, s action_id)` | `notify` | signal | A desktop popup; empty `action_label`/`action_id` means no button. |

Where it shows up:

- **Card**: the phone card's "From Plugins" section (Qt), the phone screen
  (`o`) of the terminal client, `blueferry cards [--run PLUGIN:ITEM:ACTION]`.
  A click on an item runs its `primary` action; the others are buttons. A
  plugin that crashes, times out or sends broken JSON shows a dimmed line
  with the reason instead of its items.
- **Share**: "Send to…" in the card's Tools (Qt), the tray menu, the
  terminal phone screen (`s`) and
  `blueferry send FILE… [--to PLUGIN[:TARGET]] [--list]` (`PLUGIN` is the id
  or alias; without `--to` the only target is used). Whether "Send to…"
  appears comes from the manifests; `ShareTargets` (which starts the plugin)
  is called only when the user opens it.
- **Notify**: the BlueFerry daemon shows the popup through its own
  notification sink, so the user's policy applies: `none` silences
  plugins, `messages` and `all` show them, and without
  `BLUEFERRY_SHOW_NOTIFICATION_CONTENT` only the plugin's name appears.
  The daemon reads manifests (it still runs no plugin code), accepts the
  signal only from the owner of an enabled `notify` plugin's bus name
  running as the same user (an unverified sender gets at most twelve
  lookups a minute), and shows at most six popups a minute per plugin. A click on the button calls `InvokeAction("notify", action_id,
  "{}")`. The signal itself is visible on the user's session bus; keep
  personal data out of it where you can.
- **`open_uri`** is opened only when it is `http(s)://` or a `file://`
  URI of a file or folder owned by the user in the plugin's own cache
  directory: `<cache>/<Id>/` or, with an `Alias`, `<cache>/<Alias>/`, where
  `<cache>` is `$XDG_CACHE_HOME/blueferry` or `~/.cache/blueferry`. Anything
  else is dropped.

Clients call these members from worker threads with timeouts (15 s for
`GetCardItems` and `ShareTargets`, 60 s for `InvokeAction` and
`SendFiles`), and the base service runs the hooks on its worker thread.
Before `InvokeAction` and `SendFiles` a client refuses a running plugin
owned by another user. The card asks at most eight plugins; more are
named in one dimmed line.

```python
from blueferry.plugin_api.service import CardService, NotifyService, ShareService
from blueferry.plugin_api.surfaces import Action, ActionResult, CardItem, SendResult, ShareTarget

class Calendar(CardService, NotifyService):
    def card_items(self):                       # worker thread
        return [CardItem("next", "Dentist", icon="view-calendar", subtitle="in 20 min",
                         actions=[Action("open", "Open", kind="primary")])]

    def invoke_action(self, item_id, action_id, args):   # worker thread
        return ActionResult(True, open_uri="https://calendar.example/e/1")

    def remind(self):                           # any thread
        self.emit_notify("Dentist", "in 20 minutes", "view-calendar", "Open", "open")
        self.emit_card_changed()

class Drop(ShareService):
    def share_targets(self):
        return [ShareTarget("ablage", "Ablage", "folder-cloud")]

    def send_files(self, target_id, paths):
        return SendResult(True, f"Uploading {len(paths)} files", job="upload-1")
```

`blueferry.plugin_api.testing.FakeHost` plays BlueFerry's side in tests,
with the same validation: `host.card_items()`, `host.invoke(item, action)`,
`host.share_targets()`, `host.send(target, paths)`, `host.card_changes`,
`host.notifications` and `host.click(notification)`.

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
call the service in-process through the full validation,
`ScriptedTransport` replays canned replies, and `FakeHost` drives the 1.2
surfaces (see above).

### Plugin from its own repository

A plugin lives in its own Git repository (for example
[blueferry-plugin-immich](https://github.com/joshii-h/blueferry-plugin-immich)).
Layout:

```
my-plugin/
├── pyproject.toml            # depends on blueferry-plugin-api
├── README.md
├── data/
│   └── io.example.my_plugin.plugin
├── src/blueferry_my_plugin/
│   ├── __init__.py
│   ├── __main__.py           # "serve" and the setup CLI
│   └── service.py            # PhotosService subclass
└── tests/
```

Rules: import only `blueferry.plugin_api` from BlueFerry and depend on the
`blueferry-plugin-api` distribution, not on `blueferry`:

```toml
dependencies = [
  "blueferry-plugin-api @ git+https://github.com/joshii-h/blueferry@local/battlestation#subdirectory=plugin-api",
]
```

(The ref is a branch for now and must become a release tag.) Declare a
`[project.scripts]` entry point that serves the plugin and name it in `Exec=`
(and `Cli=`). Ship exactly one manifest, in `data/` or as package data in
`src/<package>/`.

## Installing plugins

```
blueferry plugins install https://github.com/me/blueferry-plugin-x [--ref TAG|COMMIT] [--yes]
blueferry plugins update ID [--yes]
blueferry plugins remove ID [--yes]
blueferry plugins enable|disable ID
blueferry plugins config ID [--set KEY=VALUE]... [--secret KEY]...
```

The Qt settings (Plugins) and the terminal client offer the same.

- Only `https://` Git URLs. The ref is pinned: `--ref` takes a tag or a
  full commit; without it the newest version tag (`v1.2.3` or `1.2.3`) is
  used, else the default branch's HEAD, pinned as its commit.
- Install is two steps. First the ref is cloned to
  `~/.local/share/blueferry/plugins/src/<id>` and the manifest is read; no
  plugin code runs. The client shows source, ref and commit, capabilities,
  the command it will run and the settings, and asks (`--yes` skips the
  question). Only then a venv is created at
  `~/.local/share/blueferry/plugins/venvs/<id>-<commit>` (with
  `--system-site-packages` for dbus-python and PyGObject), the package is
  installed with pip (its dependencies, including `blueferry-plugin-api`,
  come from the network), and the manifest (with `Exec`/`Cli` pointing into
  the venv) and the D-Bus service file are written below `~/.local/share`.
  `Exec` and `Cli` must name a program the plugin installs, or `python`.
- A manifest with the same `Id` that BlueFerry did not install (for example
  from an earlier `setup` command) is replaced; the summary says so.
- `update` compares the pinned ref with the newest tag (or the branch HEAD
  for a pinned commit), shows both commits and the commit log, and asks.
- `remove` deletes the checkout, the venv, the manifest and the service
  file. The plugin's own settings and keyring entries stay.
- Disabled plugins stay installed; clients skip them. The list lives in
  `~/.config/blueferry/plugins.json`.
- A BlueFerry installed system-wide shadows the venv's
  `blueferry.plugin_api` (a regular package wins over the namespace
  portion). That is harmless while both are the same version.

## Plugin store (indexes)

`blueferry plugins available` (and `search TERM`) list plugins from curated
index files; Qt and the terminal client show them as cards. The default index
is `plugins-index.json` in
[joshii-h/blueferry-plugins-index](https://github.com/joshii-h/blueferry-plugins-index);
`blueferry plugins index add|remove|reset URL` changes the list.

```json
{"version": 1, "plugins": [
  {"id": "io.weirdware.blueferry.immich_photos", "name": "Immich photos",
   "description": "…", "repo": "https://github.com/joshii-h/blueferry-plugin-immich",
   "ref": "v0.2.0", "capabilities": ["photos"], "icon": "folder-pictures",
   "emoji": "", "screenshot": "", "min_blueferry": "0.8", "api_version": 1}
]}
```

- An index is untrusted: at most 256 KiB, fetched over https with a
  10-second timeout, every entry validated (https Git URL, tag-like ref,
  reverse-DNS id, plain-text name and description). Bad entries are
  dropped.
- Indexes are cached for six hours below `~/.cache/blueferry/plugin-index/`;
  without network the cached copy is shown with a note.
- An entry without `ref` is "coming soon" and cannot be installed.
- Installing from the store uses the same confirmed install as a URL.
