# BlueFerry

![BlueFerry messaging client](screenshot.png)

Use your iPhone's messages on Linux over Bluetooth.

<p align="center">
  <img src="ferry.jpg" width="320" alt="Blue Star ferry at Rhodes">
</p>

BlueFerry brings SMS, RCS, and iMessage from a paired iPhone to your Linux desktop.
You can read and reply to messages, start a conversation, search synced
contacts, and optionally mirror other iPhone notifications. There is no Mac
relay, Apple login, cloud service, or subscription.

This is still experimental software. Most development has used an iPhone 16
Pro Max on iOS 26.5, with additional successful testing on an iPhone 17 Pro Max
running an iOS 27 beta. Apple can change the Bluetooth behavior BlueFerry relies
on, so don't make it your only way to receive an important message yet.

## What works

- Receive and send SMS, RCS, and iMessage through the iPhone.
- Sync contacts, including phone numbers and Apple-ID email addresses.
- Mark messages read from the desktop.
- Use native GTK, KDE/Kirigami, Quickshell, or terminal clients.
- Keep local history encrypted with GNOME Keyring or KDE Wallet.
- Group chats, when BlueFerry can identify the participants safely.
- Optional: recent calls and missed-call notifications (off by default; see
  [Call history](#call-history-optional)).

BlueFerry only knows about messages it sees while connected; it does not
download your iCloud Messages archive. Attachments, reactions, typing
indicators, FaceTime, and complete sent-message history are not supported.
Phone calls are off by default; an experimental, opt-in oFono integration is
described under [Phone calls](#phone-calls-optional-experimental), and a
read-only recent-calls list with missed-call notices is available as a
separate opt-in (see [Call history](#call-history-optional)).

Direct conversations combine the phone numbers and email addresses that belong
unambiguously to one synced contact. Replies use the most recent incoming
address, shown as **Reply to** above the conversation. Shared addresses and
contacts that merely have the same name stay separate. Original message
addresses are retained; editing and syncing contacts updates the grouping.

Group replies are deliberately cautious. Bluetooth does not give BlueFerry a
reliable group ID or complete roster, so it disables replies when the
participants are unclear. For a named group, BlueFerry learns members only from
the people who send to it, so it asks you to confirm the full reply list once.
That saved list is kept until you delete the conversation, clear history, or
change the storage mode; it does not change the group on the iPhone. A message is filed under its group
only when BlueFerry also receives the iPhone's notification for it; without
one, it appears in the sender's one-to-one conversation.

## Install

Download the native packages for your distribution from the
[latest BlueFerry release](https://github.com/erikwb/blueferry/releases/latest).
Install `blueferry-backend` plus the client for your desktop. The backend also
includes the `blueferry-tui` terminal client.

Clicking a message notification opens its conversation in a running graphical
client, preferring the most recently used one. If none is running, BlueFerry
opens the last client you used. With no previous choice, it prefers GTK on
GNOME, Qt on KDE, and Quickshell on Omarchy/Hyprland, using an installed client
if the preferred one is unavailable.

For Arch Linux or CachyOS, download the `.pkg.tar.zst` files and install them
with pacman. For example, to install the GTK client:

```bash
sudo pacman -U ./blueferry-backend-*.pkg.tar.zst ./blueferry-gtk-*.pkg.tar.zst
```

For Debian, Ubuntu, Mint, Pop!_OS, or PikaOS, download the `.deb` files and
install them with apt:

```bash
sudo apt install ./blueferry-backend_*.deb ./blueferry-gtk_*.deb
```

For Fedora 43 or 44, download the `.fc43.noarch.rpm` files. For Fedora 45,
download the `.fc45.noarch.rpm` files. Fedora 43 and 44 use the same package
build. Install the backend and your client with dnf:

```bash
# Fedora 43 and 44; use fc45 below for Fedora 45.
sudo dnf install ./blueferry-backend-*.fc43.noarch.rpm \
  ./blueferry-gtk-*.fc43.noarch.rpm
```

Replace the GTK package with `blueferry-qt` for KDE Plasma. Arch and CachyOS
also provide `blueferry-quickshell`. The tested matrix currently covers Arch
Linux, CachyOS, Debian 13, Ubuntu 24.04 and 26.04, Linux Mint 22.3, Pop!_OS
24.04, PikaOS IV, and Fedora 43, 44, and 45. Ubuntu 24.04, Mint, and Pop!_OS do
not provide necessary Qt dependencies, so use the GTK or terminal client there.

Arch and Fedora packages set up the newer Bluetooth support needed for iPhone
system notifications. Debian-family packages do not change or restart
Bluetooth; messages and contacts still work, and notifications are added only
when that machine already supports them.

### Build packages from source

Clone the repository, then choose the build instructions for your
distribution:

```bash
git clone https://github.com/erikwb/blueferry.git
cd blueferry
```

#### Arch based distros

Install the basic build tools, then build and install all four packages:

```bash
sudo pacman -S --needed base-devel python
./build.sh -si
```

Run `./build.sh` without `-i` to build without installing. Finished packages
are written to `packaging/arch/`.

The build always uses `/usr/bin/python`, because the build and test
dependencies are Arch packages for the system interpreter. A mise, pyenv,
conda, or activated virtualenv python earlier in `PATH` is ignored rather
than producing a package for the wrong `site-packages` directory.

#### Debian based distros

```bash
sudo apt-get install devscripts equivs
sudo mk-build-deps -i -r -t 'apt-get -y --no-install-recommends' packaging/deb/control
./packaging/build-deb.sh
```

Finished packages are written to `dist/deb/`.

#### Fedora

```bash
sudo dnf install dnf-plugins-core rpm-build
sudo dnf builddep packaging/rpm/blueferry.spec
./packaging/build-rpm.sh
```

Finished packages are written to `dist/rpm/`.

#### OpenRC systems

On OpenRC, install BlueFerry's D-Bus activation file and let the session bus
start the backend; no init script is needed. An optional OpenRC user service
(OpenRC 0.62 or newer) is only for desktops whose session bus is
`$XDG_RUNTIME_DIR/bus`. Do not create one on `dbus-run-session` desktops such
as Plasma under greetd or SDDM. For iPhone system notifications, start
`bluetoothd` with `-E` (see `/etc/conf.d/bluetooth`) and run
`sudo rc-service bluetooth restart`. Without systemd, the daemon also runs
without the systemd unit's sandboxing. See
[packaging/openrc/README.md](packaging/openrc/README.md) before setting up.

See [packaging/README.md](packaging/README.md) for the exact support matrix and
more packaging details.

## Pair an iPhone

Start the client that fits your desktop:

```bash
blueferry-gtk         # GNOME, Cinnamon, and similar desktops
blueferry-qt          # KDE Plasma
blueferry-quickshell  # Quickshell
```

BlueFerry requires an adapter with Bluetooth Classic and Bluetooth 4.0 or newer
with LE advertising support. LE advertising is needed to enable the iPhone's
messages and contacts, including in compatibility mode. Bluetooth 3-only
adapters are incompatible.

Then:

1. Keep the iPhone unlocked with **Settings → Bluetooth** open.
2. Let BlueFerry check your Bluetooth controller.
3. In BlueFerry, choose **Scan**, select the iPhone, and choose **Pair**.
   BlueFerry starts the pairing request; you do not need to find and tap the
   computer under **Other Devices** on the phone.
4. When the request appears on the iPhone, approve it and confirm that both
   devices show the same code. It can take around 15 seconds to appear.
5. After pairing, tap **ⓘ** beside the computer on the iPhone and enable
   **Show Message Notifications** and **Sync Contacts**. If the toggles are
   missing, return to the Bluetooth device list and reopen the **ⓘ** page a few
   times. If iOS asks to **Allow System Notifications**, approve that too.
6. Wait for Messages and Contacts to show as connected. If you use the default
   encrypted storage, approve the desktop wallet prompt.

System Notification access lets BlueFerry recognize group-message metadata.
When it is unavailable, ordinary messages and contacts still work, but a group
message may look like a direct conversation with its sender.

### Pairing options

Most people should leave both options unchecked.

- **Compatibility pairing for iOS 18 or earlier** keeps the signal that makes
  the Messages and Contacts permissions appear, but does not connect iPhone
  system notifications. BlueFerry also chooses this automatically when the
  local BlueZ stack cannot support them.
- **Use explicit Bluetooth pairing** skips the normal connection-first
  approach and asks BlueZ to pair immediately. Try it only if normal pairing
  keeps getting canceled on that Bluetooth controller. It is independent of
  iOS compatibility mode.

For a clean retry, forget the computer on the iPhone and forget the iPhone on
Linux before pairing again. Stale phone-side Bluetooth state can survive a
one-sided forget, so reset both sides rather than repeatedly pairing over the
old record.

The terminal wizard exposes the same flow:

```bash
blueferry pair-setup
blueferry pair-setup --compatibility-mode
blueferry pair-setup --explicit-pairing
```

Once setup is complete, the backend starts automatically and reconnects after
normal Bluetooth interruptions. Package upgrades and same-version local
rebuilds are detected automatically, so an old backend process is restarted
when needed.

## Terminal client

The TUI is included in `blueferry-backend` on every supported package format.
Arch uses its repository Textual package; DEB and RPM builds carry a private
Textual 8 runtime for the TUI.

Start it with either:

```bash
blueferry-tui
blueferry tui
```

Press `?` for the keyboard map or `Ctrl+P` for the command palette. The TUI has
conversation search, a multiline composer, mouse support, themes, and a layout
that adapts to narrow terminals.

## Omarchy Quattro

The native bar panel lives in
[omarchy-blueferry](https://github.com/erikwb/omarchy-blueferry):

```bash
omarchy plugin add https://github.com/erikwb/omarchy-blueferry.git
```

Enable it from **Setup › Plugins**. Its popup shows connection health and recent
conversations; the full Quickshell client handles pairing, messages, and
preferences.

The Quickshell client follows Omarchy's active palette and system monospace
font, with compact controls and thin frames. Sent message bubbles stay blue
across themes. Outside Omarchy, it uses the desktop palette.

![Quickshell client with sample conversations](docs/images/quickshell.png)

## Notifications and local data

BlueFerry can show message notifications only—the default—all iPhone
notifications, or none. Other app notifications are displayed and discarded;
they are not added to message history. Messages seen through both MAP and ANCS
are deduplicated.

Message history and contacts are encrypted by default with a random key stored
in GNOME Keyring or KDE Wallet. If the wallet is locked, live messages continue
to work, but retained history and contact lookup wait until you unlock it. You
can also choose unencrypted storage or **Do not retain local data**. Changing
storage modes clears the existing cache so encrypted and plaintext records are
never mixed.

Starred conversations, saved group participants, and group confirmations follow
the same storage policy as history. Older plaintext preferences are encrypted
during upgrade when the wallet is available; if it is locked, those old
preferences are cleared so contact identities are no longer retained in
plaintext. You can star conversations and confirm group rosters again after
unlocking.

Configuration lives in `~/.config/blueferry`; local state lives in
`~/.local/state/blueferry`. Uninstalling packages does not delete either
directory.

The common retention settings live in `~/.config/blueferry/local.env`:

```bash
BLUEFERRY_SHOW_NOTIFICATION_CONTENT=false
BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=true
BLUEFERRY_NOTIFICATION_TIMEOUT_MS=8000
BLUEFERRY_MARK_READ_ON_DISMISS=false
BLUEFERRY_HISTORY_RETENTION_DAYS=30
BLUEFERRY_HISTORY_MAX_EVENTS=10000
BLUEFERRY_HISTORY_MAX_PAYLOAD_BYTES=268435456
```

By default, dismissing a message's desktop popup marks it read on the
iPhone too. Some notification-center "block" or do-not-disturb actions
dismiss the popup instead of just hiding it, which silently marks the message
read on the phone. Set `BLUEFERRY_MARK_READ_ON_DISMISS=false` if you don't
want desktop dismissal to ever change read state on the iPhone.

When **All iPhone Notifications** is selected, optional exact bundle-ID rules
can limit which non-Messages ANCS apps create desktop popups:

```bash
# Keep all iPhone app notifications except these apps:
BLUEFERRY_ANCS_APP_BLOCKLIST=com.example.Chat,com.example.Mail

# Or allow only these apps (omit this setting to allow every unblocked app):
# BLUEFERRY_ANCS_APP_ALLOWLIST=com.example.Calendar,com.example.Reminders
```

If both are configured, the blocklist wins. An explicitly empty allowlist
blocks every non-Messages app. Apple Messages is always processed for group
conversation metadata, but its duplicate ANCS popup remains suppressed in
favor of the MAP message popup.

Bundle IDs are case-sensitive. To discover them, follow the user-service log
while causing the app to send a notification; BlueFerry logs each app once per
daemon run without logging its notification content:

```bash
journalctl --user -u blueferry -f | grep "ANCS app observed"
```

### One-time codes

BlueFerry can copy verification codes (2FA/OTP) from newly received SMS and
iMessages to the clipboard. This is off by default because it changes the
clipboard without you doing anything:

```bash
BLUEFERRY_OTP_AUTOCOPY=true
# Optional: clear the clipboard after this many seconds (0 = keep, max 600)
BLUEFERRY_OTP_CLEAR_SECONDS=60
```

Only a message that has just arrived counts: sent messages, history, and
messages older than ten minutes are ignored. A number is treated as a code only
when a word like "code", "verification", "Bestätigungscode", "code de
vérification", or "codice" is next to it; amounts, dates, times, phone numbers,
and order or tracking numbers are skipped. `G-123456` and `123-456` are copied
as `123456`. Check what a message would copy with
`echo 'Your code is 123456' | blueferry otp-check`.

A short popup confirms the copy. It shows the code and sender only when
`BLUEFERRY_SHOW_NOTIFICATION_CONTENT` is on; with the notification setting
**None** the code is copied silently. The code is never logged, stored, or
published on BlueFerry's D-Bus API, so there is no command to show it again.

The backend copies with `wl-copy` from wl-clipboard on Wayland, or `xclip` or
`xsel` on X11; `blueferry otp-status` shows which one it finds. With
wl-clipboard 2.3 or newer the code is marked as sensitive, so Klipper and other
clipboard managers keep it out of their history; older versions and the X11
tools cannot do that. The clear timer only clears a code that is still on the
clipboard, and stopping the backend clears a code it still holds. Clearing
cannot remove an entry a clipboard manager already saved, so without the
sensitive hint the code stays in Klipper's history.

The helpers need the graphical session in the backend service's
environment. On Wayland, `WAYLAND_DISPLAY` is used, or else the only
`wayland-N` socket in `$XDG_RUNTIME_DIR`; if `wl-copy` cannot reach that
display, BlueFerry tries `xclip`/`xsel` once. X11 needs `DISPLAY` and
`XAUTHORITY` in the service environment (for example through
`systemctl --user import-environment DISPLAY XAUTHORITY`). Copying relies on
the Wayland data-control protocol, which KWin and wlroots compositors provide;
GNOME/Mutter without it is untested.

The iPhone often sends message times without a time zone, and they are read
in this computer's zone. If the phone and the computer use different zones,
codes can look older than ten minutes and are skipped; the debug log then
shows "ignoring a message N seconds old".

### iPhone notification actions (opt-in)

iOS attaches actions to some notifications, such as **Accept**/**Decline** on
an incoming call or calendar invitation, or **Clear**. With
**All iPhone Notifications** selected, you can show them as buttons on the
desktop popup:

```bash
BLUEFERRY_ANCS_ACTIONS=true
# Popups with action buttons stay longer than ordinary ones (1000-120000 ms):
BLUEFERRY_ANCS_ACTION_TIMEOUT_MS=30000
```

Clicking a button asks the iPhone to perform that action through ANCS. This is
independent of hands-free calling: *Accept* on a call answers it on the
iPhone, and the audio stays wherever iOS routes it. Nothing is sent to the
phone unless you click a labelled button. Dismissing or letting a popup
expire never touches the phone, and each popup runs at most one action.
Messages popups come from MAP and keep their existing open and dismiss
behavior.

The button text is chosen by the app that sent the notification, so it can
contain content (for example "Pay CHF 50 to Bob"). Actions therefore stay off
while `BLUEFERRY_SHOW_NOTIFICATION_CONTENT=false`: the labels are then not even
requested from the iPhone. When the iPhone connection is re-established,
popups whose buttons belonged to the previous connection are closed, because
iOS may reuse their notification numbers.

If the notification was already handled on the iPhone, or the phone
disconnected in the meantime, BlueFerry shows a short "iPhone action not
completed" notice instead. `blueferry doctor` reports whether the setting is
enabled, and `GetStatus` includes `ancs_actions`.

This has only been exercised against simulated ANCS responses so far, not a
physical iPhone.

Restart the user service after editing `local.env` settings.

### Contact photos (optional)

Contact photos are off by default. To show the iPhone's contact pictures as
avatars in the Qt conversation list and as desktop notification icons, add
this to `local.env`, restart the user service, and sync contacts:

```bash
BLUEFERRY_CONTACT_PHOTOS=true
```

The iPhone already sends photos in the normal contact download, so this
doesn't add a second Bluetooth transfer. BlueFerry just stops throwing them
away. Photos are stored in the contact cache with the same encryption, storage
mode, and replacement as the contacts, and are deleted the next time the
backend starts with the option off. Only JPEG and PNG photos up to 256 KiB and
2048×2048 pixels are kept. A photo shows only when its address belongs to
exactly one contact. The backend never decodes an image: the Qt client and the
notification server do. For notifications, the backend puts a temporary
owner-only copy of the photo under `$XDG_RUNTIME_DIR/blueferry`, passes it as
the notification's `image-path`, and deletes it when contacts change or the
backend stops. Plasma is expected to show that image. Neither Plasma nor any
other notification server has been tested with it yet, so a server that
ignores `image-path` just shows the usual icon. Before going back to a
BlueFerry release without this option, turn it off and start the backend once.
That start erases the stored photos; an older release doesn't know about them
and would keep them indefinitely. `blueferry contacts-photo NAME -o FILE`
exports one cached photo. The GTK, terminal, and Quickshell clients don't show
avatars yet.

### Clicking iPhone app notifications

Clicking a message popup opens the conversation in BlueFerry. Other app
popups (**All iPhone Notifications**) do nothing when clicked unless you add
a click rule for that app. A rule maps one exact bundle ID to either an
`http`/`https` address, opened in your default browser, or a desktop entry
ID, launched like any installed app:

```bash
blueferry notifications open-map set com.apple.mobilemail org.mozilla.Thunderbird.desktop
blueferry notifications open-map set net.whatsapp.WhatsApp https://web.whatsapp.com
blueferry notifications open-map set com.tinyspeck.chatlyio com.slack.Slack.desktop
blueferry notifications open-map list
blueferry notifications open-map remove net.whatsapp.WhatsApp
```

The Qt client edits the same rules under **Desktop Notifications** while all
notifications are shown. Rules are stored with the other popup preferences in
`~/.config/blueferry/settings.json` (owner-only, not encrypted) and take
effect without restarting the service.

Rules only ever open the fixed address or app you configured. Nothing from
the notification (title, text, sender) is added to the address or passed to
the app, and no shell is involved. Addresses must be plain `http(s)` URLs
without credentials, quotes, spaces or other characters that need escaping;
`javascript:`, `file:` and custom app schemes are rejected. Desktop entries
must be bare IDs ending in `.desktop` (no paths, arguments or commands). Find
an app's ID with `ls /usr/share/applications ~/.local/share/applications
/var/lib/flatpak/exports/share/applications`. Apps without a rule, and Apple
Messages, keep their normal behaviour.

When WirePlumber 0.5 or newer is installed, BlueFerry keeps calls and music on
the iPhone by writing
`~/.config/wireplumber/wireplumber.conf.d/99-blueferry-keep-phone-audio.conf`
before pairing and waiting for WirePlumber to reload it. The fragment removes
the adapter roles that make this computer an A2DP/HFP sink, and disables
auto-connect on phone cards so a later `bluetooth-a2dp-autoconnect` rule cannot
steal the stream. A failed pairing attempt removes a fragment that attempt
installed. After a successful bond the daemon keeps reconciling the same
file. Set `BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false` to remove BlueFerry's
fragment only. With the optional phone calls enabled, the fragment keeps the
hands-free roles (`hfp_hf`, `hsp_hs`) and still removes `a2dp_sink`: music
stays on the iPhone, calls can come to this computer.

## Phone calls (optional, experimental)

BlueFerry can answer, decline, place, and hang up iPhone calls through
[oFono](https://git.kernel.org/pub/scm/network/ofono/ofono.git)'s Hands-Free
Profile support. This is **off by default** and not needed for messaging.
An earlier HFP experiment was removed from BlueFerry because oFono and
PipeWire's native HFP backend race for the same BlueZ profile (see
[Historical HFP result](PROTOCOL.md#historical-hfp-result)); this opt-in
integration leaves that choice and its setup to you, adds no package
dependency, and keeps working normally when oFono is missing.

The integration was developed against oFono 2.18 and is **not yet verified
end-to-end on hardware**; treat it as a preview.

Requirements:

- oFono running as a system service, with its HFP hands-free plugin.
  BlueFerry never starts oFono itself (its calls carry D-Bus NO_AUTO_START).
- oFono's D-Bus policy must admit your user. oFono's shipped `ofono.conf`
  (for example `/etc/dbus-1/system.d/ofono.conf` or
  `/usr/share/dbus-1/system.d/ofono.conf`, depending on the distribution)
  only allows root and `at_console` sessions; a desktop without console tracking gets
  `AccessDenied` and BlueFerry reports calls as **unavailable** (logged once).
  A minimal drop-in, for example `/etc/dbus-1/system.d/ofono-local.conf`:

  ```xml
  <!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
   "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
  <busconfig>
    <policy user="your-login">
      <allow send_destination="org.ofono"/>
    </policy>
  </busconfig>
  ```

  Security meaning: every process running as that user (or, with
  `<policy group="…">`, as any member of that group) may fully control oFono,
  i.e. place, answer, and end calls and change settings of every modem oFono
  manages, not only BlueFerry. Prefer `user=` over a broad group. The system
  bus normally picks up the new file by itself; otherwise reload it.
- PipeWire/WirePlumber configured to hand HFP to oFono. Your own fragment
  should only select the backend, for example
  `~/.config/wireplumber/wireplumber.conf.d/51-bluez-ofono.conf`:

  ```text
  monitor.bluez.properties = {
    bluez5.hfphsp-backend = "ofono"
  }
  ```

  Do not set roles there: BlueFerry's `99-` phone-audio fragment (above)
  overrides `bluez5.roles` and, with calls enabled, keeps `hfp_hf` and
  `hsp_hs` while still removing `a2dp_sink`. If you set
  `BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false`, BlueFerry manages no roles and
  your own `bluez5.roles` must include `hfp_hf`.

Enable it in `~/.config/blueferry/local.env` and restart the user service:

```bash
BLUEFERRY_CALLS_ENABLED=true
```

Toggling the flag rewrites the phone-audio fragment. BlueFerry restarts
`wireplumber.service` only through systemd; on hosts where WirePlumber is not
a systemd user service (for example OpenRC with a session launcher), restart
WirePlumber yourself, e.g. `gentoo-pipewire-launcher restart`. The backend
log then says "WirePlumber fragment changed; restart WirePlumber to apply".

What happens then:

- The backend looks for the iPhone's oFono modem (type `hfp`, path ending in
  `dev_XX_XX_XX_XX_XX_XX` for the paired phone). iOS does not power this modem
  up by itself: BlueFerry sets `Powered=true` while the Classic link is up,
  waits for oFono to confirm it, then sets `Online=true`. Call control is
  ready once the modem is online and lists oFono's call manager. It also
  raises oFono's call volume to 100 % because the 50 % default is nearly
  inaudible with an iPhone.
- An incoming call shows a desktop notification with **Answer** and
  **Decline** (without the caller when
  `BLUEFERRY_SHOW_NOTIFICATION_CONTENT=false`; the contacts-only notification
  setting deliberately does not apply to calls). The Qt client has a
  **Phone Calls** dialog, the terminal client a calls panel (`c`) and an
  "Incoming call" notice, and the CLI a `calls` command group:

  ```bash
  blueferry calls                 # state and current calls
  blueferry calls dial '+41 79 123 45 67'
  blueferry calls answer          # the ringing call
  blueferry calls dtmf 1234#      # tones on the active call
  blueferry calls hangup          # or: hangup --all
  ```

- `dial` accepts plain numbers only (an optional leading `+` and digits;
  spaces, dashes, dots, parentheses, and slashes are ignored). `*` and `#`
  are refused: dialed, they form service codes such as `**21*…#` that
  reconfigure the phone (call forwarding) rather than place a call. Use
  `dtmf` for keypad symbols during a call.
- With a second call, answering holds the active call (HoldAndAnswer);
  `swap` and `hold-answer` are available, but "release and answer" is not
  exposed. Hanging up the held call of two relies on the phone supporting
  `AT+CHLD=1x` through oFono and still needs hardware verification.
- If oFono is not installed, not running, or denies access, calls report
  **unavailable**; BlueFerry notices when oFono starts. Call details are only
  returned by the private `Calls1.ListCalls` method; the broadcast
  `CallsChanged` signal carries no content. Calls are not written to message
  history.

### Phone battery, signal, and network

With the calls integration enabled, BlueFerry also shows the phone's battery
level, signal strength, and network (operator) name, as oFono reports them
from the standard HFP indicators. oFono publishes them as soon as the
iPhone's hands-free modem is powered, so they appear even while call control
is still waiting for the modem to go online (for example when the
oFono/WirePlumber profile race keeps it offline). Nothing else needs
configuring:

```bash
blueferry phone-status          # Battery: about 60 % / Signal: 80 % / Network: …
blueferry phone-status --json
```

The Qt client shows a small battery and signal indicator next to
"Conversations" (hover for the network name); the terminal client, the
Quickshell header, and the GTK status page add battery and signal to their
connection line. When calls are disabled, oFono is missing, or the modem is
not powered, the values are simply unknown and nothing is shown.

Side effect: oFono answers the first request for the battery by asking the
phone for its own number (`AT+CNUM`) and caches it. BlueFerry discards that
number (`SubscriberNumbers`) and never stores, logs, or returns it. While
oFono waits for the phone, it refuses other readers with `InProgress`;
BlueFerry then retries once after 30 s. The operator name is what the phone
reports through `AT+COPS` (HFP allows up to 16 characters) and is empty
while the phone is not registered to a network.

Granularity is coarse: iPhones report their battery to hands-free devices in
six steps (0-5), so BlueFerry shows 0, 20, 40, 60, 80, or 100 %, and the
signal likewise moves in 20 % steps. iOS also sends a finer 0-9 battery level
through Apple's `AT+IPHONEACCEV` extension, but stock oFono does not decode
it and BlueFerry does not patch oFono. There is no charging indicator.

An optional desktop warning fires once when the battery reaches the
threshold and again only after the phone has charged at least one step
(20 %) above it. The daemon keeps no record of past warnings, so after a
restart of BlueFerry a phone that is still low is reported once more:

```bash
BLUEFERRY_PHONE_BATTERY_NOTIFY=true         # default off
BLUEFERRY_PHONE_BATTERY_LOW_PERCENT=20      # 0-80, default 20
```

The values are part of the private `GetStatus` reply (keys
`phone_battery_level`, `phone_signal_strength`, `phone_network_name`,
`phone_network_status`; `null` when unknown). Changes are announced with the
existing argument-free `StatusChanged` signal, coalesced to at most one per
main-loop iteration; no value is ever broadcast, and the logs never contain
the levels or the operator name.

Troubleshooting: if `blueferry calls` stays at **searching** although the
iPhone is connected, the likely cause is the startup-order race between oFono
and WirePlumber for the HFP profile. Restart oFono after WirePlumber
(`sudo rc-service ofono restart` on OpenRC, `sudo systemctl restart ofono` on
systemd), then check the backend log for the modem. Audio routing itself is
PipeWire's job; BlueFerry only controls the call.

## Call history (optional)

BlueFerry can mirror the iPhone's **Recents** list (incoming, outgoing, and
missed calls) and show a desktop notification for new missed calls. This is
off by default because it retains who called you and when. It reuses the
existing PBAP connection, so the iPhone's **Sync Contacts** permission is all
it needs; BlueFerry never places, answers, or listens to calls.

```bash
BLUEFERRY_CALL_HISTORY_ENABLED=true
# Popups for new missed calls (default true when call history is enabled):
BLUEFERRY_MISSED_CALL_NOTIFICATIONS=true
# How often to ask the iPhone for its call lists, in seconds (60–86400):
BLUEFERRY_CALL_HISTORY_INTERVAL_SEC=300
```

- The list follows the same storage mode as message history: encrypted with
  the wallet key by default, unencrypted if you chose that, and not kept at all
  with **Do not retain local data**. Calls older than
  `BLUEFERRY_HISTORY_RETENTION_DAYS` are removed. **Clear history** and
  changing the storage mode erase it immediately; after turning the option off
  again, it is erased the next time the daemon starts (restarting the service
  to apply the setting does that). Because the
  missed-call check compares against that stored list, neither the list nor
  missed-call popups work while the wallet is locked or with **Do not retain
  local data**.
- BlueFerry mirrors the phone's current lists; it is not a separate archive.
  Calls you delete on the iPhone disappear on the next refresh.
- Bluetooth has no "new call" event for this, so the list is refreshed every
  `BLUEFERRY_CALL_HISTORY_INTERVAL_SEC` seconds (and on demand). A missed-call
  popup can therefore arrive up to that long after the call.
- Messages come first. Contacts and call history share one Bluetooth transfer
  queue with messages, so automatic refreshes pause while the message
  connection is reconnecting and resume once it is back. If messages have
  never connected, the list is fetched once after a three-minute grace period
  and then only on demand. Each of the three call lists must finish within
  two minutes. `--sync` and **Refresh from iPhone** are never held back.
- With phone calls (`BLUEFERRY_CALLS_ENABLED`) also on, a call that stops
  ringing unanswered is announced right away through hands-free, and call
  history is refreshed after every call. Its later entry for the same call
  (same number, within two minutes) is then not announced a second time; a
  call you declined from the BlueFerry popup is not announced at all. This
  pairing is kept in memory for ten minutes only.
- The first refresh after enabling the option (or after clearing history)
  only records the existing list; you are not flooded with old missed calls.
  Each missed call is announced once, calls older than 12 hours are never
  announced (for iPhone timestamps without a time zone, "12 hours" is measured
  in this computer's time zone), and more than three at once are summarized in
  one popup.
- Popups follow the same rules as message popups: **No notifications**
  silences them, **Only from contacts** skips unknown callers, and
  `BLUEFERRY_SHOW_NOTIFICATION_CONTENT=false` hides the caller and time. They
  appear with the default **Messages** setting, not only with **All iPhone
  notifications**: a missed call is person-to-person communication like a
  message, and enabling the option is your consent.

View the list with `blueferry calls-history` (`--missed`, `--limit N`,
`--sync` to refresh from the iPhone first) or **Recent Calls** in the KDE
client's menu. The GTK, terminal, and Quickshell clients do not show it yet.

## iPhone media control (optional)

BlueFerry can show what the iPhone is playing and send play, pause, next,
previous, volume, skip and like/dislike commands through Apple's Media
Service (AMS). AMS uses the same Bluetooth LE bond as notifications, so it
needs the full pairing mode; compatibility mode never connects LE. It is off
by default because it adds Bluetooth traffic and is outside BlueFerry's
messaging focus. Opt in in `~/.config/blueferry/local.env` and restart the
user service:

```bash
BLUEFERRY_MEDIA_CONTROL_ENABLED=true
# Optional: also publish the iPhone as an MPRIS player for Plasma, media keys
# and playerctl. See the privacy note below before enabling it.
BLUEFERRY_MEDIA_MPRIS_ENABLED=true
```

```bash
blueferry media            # now playing
blueferry media toggle     # also: play, pause, next, previous, volume-up,
                           # volume-down, skip-forward, skip-backward, like, ...
```

Only commands the iPhone currently offers are sent; for example, like/dislike
exist only for players that advertise them. The Kirigami client shows a small
now-playing bar above the conversations while a player is active.

With the MPRIS option, BlueFerry registers
`org.mpris.MediaPlayer2.blueferry_iphone` on your session bus while the iPhone
reports an active player. **MPRIS is public within your login session:** any
application you run can read the current title, artist and album and is
notified of changes, exactly as with a desktop music player. Without the MPRIS
option, track details are only available through BlueFerry's own
authenticated D-Bus API, and its change signal carries no content. AMS has no
absolute volume, seek or stop, so through MPRIS a volume change moves the
iPhone one step, seeking is not offered (use `blueferry media skip-forward` or
`skip-backward` for the phone's fixed skips), and Stop pauses playback.

BlueFerry deliberately does not use AVRCP for this: acting as an AVRCP
controller could make the iPhone route its audio to this computer.

## Internet sharing (experimental)

BlueFerry can also use the iPhone's Personal Hotspot over Bluetooth (PAN).
It never does this on its own: turn it on with **Share iPhone Internet** in
the KDE client's iPhone Settings or with `blueferry tether on`, and off the
same way. This has only been exercised against simulated BlueZ and
NetworkManager services, not yet with a real iPhone.

1. On the iPhone, open **Settings → Personal Hotspot** and turn on **Allow
   Others to Join**. When it is off the connection usually fails, and
   BlueFerry points you here.
2. Make sure BlueFerry's normal connection to the iPhone is up. Tethering
   uses that existing Bluetooth link and never connects or disconnects the
   phone itself, so messages, contacts and notifications keep working.
3. Run `blueferry tether on` or flip the switch.

With NetworkManager running, BlueFerry asks it to activate the phone's
Bluetooth network (PAN) profile. If one already exists, for example because
you connected through the Plasma network applet before, BlueFerry reuses it
as is and never changes or deletes it. Only when there is none does it create
"BlueFerry iPhone hotspot": visible only to your user, with autoconnect
turned off, so NetworkManager does not tether by itself because of it.
NetworkManager handles the address and DNS. If NetworkManager reports a permission error, the daemon is
probably not part of an active desktop session as far as polkit is
concerned.

Without NetworkManager, BlueFerry brings up only the Bluetooth link and prints
the interface name (usually `bnep0`). Run your own DHCP client on it, for
example `sudo dhcpcd bnep0`; BlueFerry never runs privileged commands. That
link belongs to the daemon's D-Bus connection, so it ends when the daemon
stops.

The kernel needs Bluetooth BNEP support (`CONFIG_BT_BNEP`), and BlueZ needs
its network plugin. Optional settings in `~/.config/blueferry/local.env`:

```bash
# Tether automatically once messages are connected (off by default).
# `blueferry tether off` pauses this until the next explicit `on`.
BLUEFERRY_TETHER_AUTOCONNECT=false
# auto (default) prefers NetworkManager; bluez forces the link-only mode.
BLUEFERRY_TETHER_BACKEND=auto
```

Even if you never use tethering, this version changes a few things in the
background:

- The daemon exports the `Tether1` D-Bus interface and watches the phone's
  Bluetooth network state (read-only).
- A tether started elsewhere, for example from the Plasma network applet, is
  shown as active in BlueFerry and can be turned off from it.
- While a tether link demonstrably exists (its network interface is present),
  BlueFerry skips its last-resort Bluetooth adapter power cycle so it does not
  cut your connection. This applies to applet-started tethers too.
- Turning a tether off in the network applet counts as a deliberate stop:
  automatic tethering does not bring it back until you turn it on again.

## Lock when the iPhone goes away

BlueFerry can lock your desktop session after the paired iPhone has been
disconnected for a while. It is off by default. Turn it on in the Qt client's
iPhone settings (**Away Lock**) or from a terminal:

```bash
blueferry proximity-lock enable --grace 60   # opt in, lock after 60 s away
blueferry proximity-lock status
blueferry proximity-lock test                # dry run, never locks
blueferry proximity-lock disable
```

`BLUEFERRY_PROXIMITY_LOCK=true` and `BLUEFERRY_PROXIMITY_LOCK_GRACE_SEC=60`
(10–3600) in `local.env` set the initial values; a choice saved through a
client or the CLI takes precedence, and the daemon logs once at startup when
it ignores a differing `local.env` or environment value for that reason.

**This is a lock trigger, not a security feature.** Bluetooth presence can be
relayed or spoofed, and the phone turning off Bluetooth looks the same as the
phone leaving. BlueFerry therefore only ever *locks*; it never unlocks the
desktop when the iPhone returns, and you should keep your normal password or
PIN. Use it as a "forgot to lock" safety net.

How it decides:

- Presence is the Bluetooth link state BlueFerry already maintains (Classic
  or LE connected). There is no extra scanning and no signal-strength check.
- It arms only after it has seen the iPhone connected since the service
  started, the system resumed, or the last lock.
- A disconnect starts the grace period; reconnecting within it cancels the
  lock. After locking once it waits until the iPhone is seen again.
- It never locks while the system is suspending, while desktop Bluetooth is
  off, during Bluetooth discovery or pairing, while BlueFerry itself is
  recovering the adapter, or after the iPhone is forgotten.
- When BlueZ reports that this computer ended the connection (for example
  "Disconnect" in a desktop Bluetooth applet), the lock pauses until the
  iPhone is connected again. Older BlueZ versions without this report
  simply do not pause.

Locking uses `org.freedesktop.ScreenSaver.Lock` on the session bus (KDE
Plasma and others) and falls back to `org.freedesktop.login1.Session.Lock`
for your own logind or elogind session. KDE answers only once its lock
screen is up; a slow answer is reported as `screensaver-requested` and does
not fall back to logind. If screen locking is disabled by policy (for
example a KDE Kiosk `lock_screen=false` restriction), Plasma reports the
request as successful without locking, and BlueFerry cannot tell.

## Command line

The graphical clients cover normal use, but the CLI is useful for diagnostics
and scripts:

```bash
blueferry sms-list
blueferry sms-send '+15551234567' 'on my way'
blueferry sms-send person@icloud.com 'hello from Linux'
blueferry sms-send Alice 'running late'
blueferry contacts-sync
blueferry calls-history --missed   # only with BLUEFERRY_CALL_HISTORY_ENABLED
blueferry contacts-photo Alice --output alice.jpg   # needs BLUEFERRY_CONTACT_PHOTOS
blueferry media status
blueferry history-clear
blueferry otp-status
blueferry notifications open-map list
blueferry doctor
blueferry tether            # status; also: tether on, tether off
```

Ambiguous contact names are presented for you to choose from rather than
guessed.

## Troubleshooting

Start with the iPhone page in the app. It reports Messages, Contacts, and iPhone
Notifications separately; messages and contact sync can work even when the
optional notification connection does not.

For logs and prerequisite checks:

```bash
blueferry doctor
journalctl --user -u blueferry -f
```

With the optional OpenRC user service, the backend log is
`~/.local/state/blueferry/daemon.log`.

If messages work but names do not, use **Sync Contacts** or run
`blueferry contacts-sync`.

If iPhone notifications never connect and the app or `blueferry doctor`
says the Bluetooth pairing looks outdated, the LE half of the pairing is
stale. This usually happens when the pairing was removed on only one side,
so the iPhone no longer has the key this computer uses. The LE link then
connects about every two seconds and drops right away, with the log
repeating LE reconnects. `btmon` shows `LE Start Encryption` failing,
followed by a disconnect with reason 0x08 (supervision timeout). BlueFerry
stops its own LE connection attempts and warns once. To fix it:

1. On the iPhone, open Settings > Bluetooth, tap (i) next to this computer,
   and choose **Forget This Device**.
2. On this computer, run `bluetoothctl remove <iPhone address>`, using the
   address `blueferry doctor` shows as the target. The backend stops when
   the pairing disappears.
3. Pair the iPhone again from the app.

If notifications previously worked with the same phone and adapter but stay
unavailable for five minutes, BlueFerry can attempt one adapter power cycle.
It first tries an LE-only reset and checks that the phone still answers a
read-only MAP request. Automatic cycling is skipped if another Bluetooth
device is paired or connected to that adapter, discovery is active, or BlueFerry
is transferring data. It requires BlueZ to report power transitions and
respects Bluetooth being turned off and explicit permission failures.
The attempt limit survives backend restarts: another cycle requires ten minutes
of verified notification connectivity, and cycles are at least an hour apart.
If a power request times out, the running backend keeps checking the original
controller and retries restoration without issuing another power-off request.
Pending restoration also survives backend restarts and finishes before normal
Bluetooth setup resumes. Once power-on is confirmed by a reply or observed
after power-off, restoration ends; a later manual power-off is left alone.
Once power-off has been requested, a failed journal update does not prevent
power-on. Failed journal cleanup is retried without pausing messaging; further
automatic power cycles wait until cleanup succeeds.
Recovery state is refreshed after acquiring the backend's D-Bus name, and a
second daemon that cannot acquire it does not attempt restoration during
shutdown.
Restoration stops if the controller is unplugged, bluetoothd or the system bus
restarts, the configured phone changes, or rfkill blocks it.
D-Bus cannot make checking an adapter and changing its power atomic;
BlueFerry watches for changes and rechecks immediately before requesting power-off.
Recovery decisions and failures appear in the backend journal.

Pairing failures save a scrubbed report that can be attached to a GitHub issue.
It includes the package build and source SHA, pairing mode, controller details,
and an ordered setup timeline. Please also include the iPhone model and iOS
version. Reports remove Bluetooth addresses and home-directory paths, but it is
still sensible to inspect anything before posting it publicly.

## Technical details

BlueFerry uses three standard Bluetooth services:

- MAP over Bluetooth Classic carries messages, read state, and sends.
- PBAP over Bluetooth Classic supplies contacts.
- ANCS over Bluetooth LE supplies optional notifications and group-message
  display information.

One unprivileged per-user backend owns those connections and exposes a small
session D-Bus API to the clients. Quickshell messaging reaches that API through
one persistent stdin bridge; its short-lived command helpers are limited to
setup before the backend is configured. Pairing and unpairing require approval
in the initiating BlueFerry client and use normal Bluetooth confirmation;
there is no hidden Apple protocol.

The deeper design and protocol notes live in
[ARCHITECTURE.md](ARCHITECTURE.md), [PROTOCOL.md](PROTOCOL.md),
[TESTING.md](TESTING.md). Release history is in [CHANGELOG.md](CHANGELOG.md).

BlueFerry began from
[iphonebridge](https://github.com/gabrielmeir53/iphonebridge), created by Gabe
Shatunovsky. Parts of the optional oFono call controller are adapted from
[tincan](https://github.com/quad341/tincan) under the MIT License. The ANCS constants and wire-format code are adapted from
[ANCS4Linux](https://github.com/bmh129/ancs4linux), by Paweł Zmarzły and
Bradley Harmon, under GPL-2.0-or-later.

BlueFerry is licensed under [GPL-2.0-or-later](LICENSE).
