"""Render the Qt main window with fake data to PNG files for visual review.

Not a test. Run from the worktree root:

    PYTHONPATH=src python tests/tools/render_main.py [OUTPUT_DIR]

Each theme renders in its own process because Qt reads the colour scheme once
at start-up. The fake bridge mirrors the recorder in test_qml_presenters.py
and never talks to D-Bus.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - re-runs this script with a different theme
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_QML = ROOT / "src" / "blueferry" / "qt" / "qml" / "Main.qml"
SCHEMES = {"dark": "BreezeDark", "light": "BreezeLight"}
SIZES = ((1500, 700), (900, 700))

LONG_BODY = (
    "DPD  Paketbestätigung\n\nGegen 9.33 Uhr bin ich am Wohnhaus angekommen.\n\n"
    "Die Einheitsnummern an der Klingel waren leider nicht eindeutig.\n\n"
    "Die Sendung wurde sicher im lokalen Depot aufbewahrt.\n\n"
    "https://mydpd.example/track/ch"
)

THREADS = [
    {
        "key": "dpd", "name": "+212786524411", "recipients": ["+212786524411"],
        "is_group": False, "unread": True, "starred": False, "reply_ready": True,
        "messages": [
            {"handle": "2", "body": "Danke, ich hole das Paket morgen ab.", "outgoing": True,
             "display_timestamp": "Today 09:52"},
            {"handle": "3", "body": "Antworten Sie bitte mit \"JA\", um die Abholung zu "
             "bestätigen. Die Sendung kann bis zum 2. Oktober abgeholt werden.",
             "outgoing": False, "display_timestamp": "Today 10:03"},
            {"handle": "4", "body": "JA", "outgoing": True, "display_timestamp": "Today 10:04"},
            {"handle": "1", "body": LONG_BODY, "outgoing": False,
             "display_timestamp": "Today 10:41"},
        ],
    },
    {
        "key": "anna", "name": "Anna Muster", "recipients": ["+41790000001"],
        "is_group": False, "unread": False, "starred": True, "reply_ready": True,
        "messages": [{"handle": "5", "body": "Kommst du heute Abend auch zum Essen? "
                      "Wir treffen uns um sieben beim Italiener am See.",
                      "outgoing": False, "display_timestamp": "Yesterday 18:20"}],
    },
    {
        "key": "family", "name": "Familie", "recipients": ["+41790000002", "+41790000003"],
        "is_group": True, "unread": False, "starred": False, "reply_ready": True,
        "messages": [{"handle": "6", "body": "Fotos vom Wochenende sind hochgeladen!",
                      "outgoing": False, "sender": "Papa",
                      "display_timestamp": "Mon 12:01"}],
    },
    {
        "key": "empty", "name": "Bank", "recipients": ["Bank"], "is_group": False,
        "unread": False, "starred": False, "reply_ready": False, "messages": [],
    },
]

STATUS = {
    "daemon": True, "map": True, "calls_enabled": True, "calls_state": "ready",
    "phone_battery_level": 80, "phone_battery_percent": 87,
    "phone_battery_source": "ble", "phone_signal_strength": 4,
    "phone_network_name": "Sunrise", "proximity_lock": "idle",
    "proximity_lock_enabled": False, "media_control_enabled": True,
    "mirror_iphone_removals": True,
}

def _feature(value, source="default", restart=False, variable=""):
    return {"value": value, "running": value != restart, "source": source,
            "variable": variable, "restart_required": restart}


FEATURES = {"loaded": True, "available": True, "notice": "", "items": {
    "calls_enabled": _feature(True, "local.env", variable="BLUEFERRY_CALLS_ENABLED"),
    "call_history_enabled": _feature(True, "settings", True, "BLUEFERRY_CALL_HISTORY_ENABLED"),
    "missed_call_notifications": _feature(True),
    "phone_battery_notify": _feature(False),
    "keep_phone_audio_on_phone": _feature(False, "local.env"),
    "media_control_enabled": _feature(True, "local.env"),
    "media_mpris_enabled": _feature(False),
    "contact_photos": _feature(True, "environment", variable="BLUEFERRY_CONTACT_PHOTOS"),
    "show_notification_content": _feature(True), "ancs_actions": _feature(False),
    "mark_read_on_dismiss": _feature(True), "otp_autocopy": _feature(True, "settings"),
    "notification_history": _feature(False), "tether_autoconnect": _feature(False),
}}
IMMICH_URL = "https://github.com/joshii-h/blueferry-plugin-immich"
PLUGINS = {
    "loaded": True, "busy": "", "message": "", "messageOk": True, "ignored": [],
    "plugins": [{
        "id": "io.weirdware.blueferry.immich_photos", "name": "Immich photos",
        "version": "0.2.0", "alias": "immich", "capabilities": ["Photos"],
        "source": IMMICH_URL, "ref": "v0.2.0", "managed": True, "enabled": True,
        "state": "ok", "stateText": "Ready", "detail": "", "hasConfig": True,
    }],
    "pending": {}, "config": {
        "id": "io.weirdware.blueferry.immich_photos", "name": "Immich photos",
        "loaded": True, "errors": {"camera_model": "is too long"}, "fields": [
            {"key": "url", "label": "Server URL", "type": "url", "required": True,
             "help": "For example https://photos.example.org", "choices": [],
             "minimum": 0, "maximum": 0, "value": "https://photos.joshuahirsig.xyz",
             "stored": False},
            {"key": "api_key", "label": "API key", "type": "secret", "required": True,
             "help": "Needs asset.read, asset.view and asset.download.", "choices": [],
             "minimum": 0, "maximum": 0, "value": "", "stored": True},
            {"key": "camera_model", "label": "Camera model", "type": "string",
             "required": False, "help": "Only photos from this camera, e.g. iPhone 16 Pro.",
             "choices": [], "minimum": 0, "maximum": 0, "value": "", "stored": False},
        ]},
    "store": {"loaded": True, "problems": [], "items": [
        {"id": "io.weirdware.blueferry.immich_photos", "name": "Immich photos",
         "description": "Recent photos and videos from your own Immich server.",
         "icon": "folder-pictures", "emoji": "", "badges": ["Photos"], "repo": IMMICH_URL,
         "ref": "v0.2.0", "installedRef": "v0.2.0", "screenshot": "", "state": "installed",
         "stateText": "Installed \u2713", "installable": False},
        {"id": "io.weirdware.blueferry.calendar", "name": "Calendar",
         "description": "Upcoming events next to your messages.", "icon": "view-calendar",
         "emoji": "", "badges": ["Calendar"], "repo": IMMICH_URL, "ref": "",
         "installedRef": "", "screenshot": "", "state": "soon", "stateText": "Coming soon",
         "installable": False},
        {"id": "io.weirdware.blueferry.webdav", "name": "WebDAV files",
         "description": "Send files from the iPhone to a WebDAV share.", "icon": "folder-cloud",
         "emoji": "", "badges": ["Files"], "repo": IMMICH_URL, "ref": "v0.1.0",
         "installedRef": "", "screenshot": "", "state": "install", "stateText": "Install",
         "installable": True},
    ]},
    "indexes": ["https://raw.githubusercontent.com/joshii-h/blueferry-plugins-index/main/plugins-index.json"],
    "defaultIndex": "https://raw.githubusercontent.com/joshii-h/blueferry-plugins-index/main/plugins-index.json",
}

def _call(day, clock, caller, address, direction, count=1, known=True):
    return {"day": day, "clock": clock, "caller": caller, "address": address, "known": known,
            "direction": direction, "missed": direction == "missed", "count": count,
            "key": address}


_ALL_CALLS = [
    _call("Today", "2:36 PM", "Anna Muster", "+41790000001", "missed", 3),
    _call("Today", "11:02 AM", "+41 44 123 45 67", "+41441234567", "incoming", known=False),
    _call("Today", "9:15 AM", "Papa", "+41790000003", "outgoing"),
    _call("Yesterday", "8:40 PM", "Anna Muster", "+41790000001", "incoming"),
    _call("Yesterday", "1:05 PM", "DPD Kundendienst", "+41848000000", "missed"),
    _call("Friday", "6:30 PM", "Familie Hirsig", "+41790000002", "outgoing", 2),
    _call("Sep 28", "10:00 AM", "Zahnarzt", "+41445550000", "incoming"),
]
CALL_ROWS = {"all": _ALL_CALLS, "missed": [row for row in _ALL_CALLS if row["missed"]]}

BRIDGE_QML = """
import QtQuick
QtObject {
    property var calls: []
    property var phoneCalls: []
    property string callsState: "ready"
    property var status: (%(status)s)
    property var threads: (%(threads)s)
    property var devices: []
    property var contactResults: []
    property var nowPlaying: ({available: true, track: {title: "E640. Kampf an zwei Fronten",
        artist: "Geschichten aus der Geschichte"}, player: {state: "playing"},
        supported_commands: ["previous", "toggle", "next"]})
    property var tether: ({available: true, state: "disconnected",
        summary: "Not sharing the iPhone's internet connection."})
    property var featureHints: ({})
    property var reconnect: ({available: true, offered: false, hint: ""})
    function reconnectPhone() {}
    property var photos: ({})
    function watchPhotos(watched) {}
    function refreshPhotos() {}
    function openPhoto(photoId) {}
    property var phoneAudio: ({supported: true, available: true, onPc: true, pending: false,
        hint: "iPhone sound plays on this computer."})
    property string phoneName: "Joshua's iPhone"
    property var notifications: []
    property var notificationsInfo: ({})
    property var compatibility: ({})
    property var onboardingCompatibility: compatibility
    property bool compatibilityLoaded: true
    property bool bluetoothActive: true
    property bool busy: false
    property bool configured: true
    property bool targetSaved: true
    property bool setupLoaded: true
    property string configuredMac: ""
    property string onboardingStage: "ready"
    property string errorText: ""
    property string pairingIssueReport: ""
    property string version: "render"
    property string bluetoothRestartCommand: ""
    property bool callHistoryEnabled: true
    property var callHistory: []
    property var callHistoryRows: (%(call_rows)s)
    function syncCallHistory() {}
    function loadCallHistory() {}
    function findContacts(query) {
        contactResults = query.length >= 2 ? [{name: "Anna Muster", address: "41790000001"},
            {name: "Andreas Beispiel", address: "41790000004"}] : []
    }
    function dialCall(number) {}
    function answerCall(callId) {}
    function hangupCall(callId) {}
    function swapCalls() {}
    function refreshCalls() {}
    function sendMessage(recipient, body) {}
    property string callHistoryError: ""
    property int avatarRevision: 0
    property var companionTools: ({probed: true, busy: "", needsPairing: true, messageOk: false,
        message: "This computer is not trusted by the iPhone yet. Unlock the iPhone and confirm \u201cTrust This Computer\u201d, then try again.",
        tools: [
        {key: "mirror", installed: true, enabled: true, active: false, title: "Mirror iPhone screen",
         subtitle: "On the iPhone, open Control Center, tap Screen Mirroring and choose this computer."},
        {key: "send", installed: false, enabled: false, active: false, title: "Send a file (LocalSend)",
         subtitle: "Install LocalSend (Flathub) to exchange files over Wi-Fi."},
        {key: "photos", installed: true, enabled: true, active: true, title: "iPhone photos (USB)",
         subtitle: "The camera roll is open. Eject it before unplugging."},
        {key: "eject", installed: true, enabled: true, active: false, title: "Eject iPhone photos",
         subtitle: "Unmount the camera roll."}]})
    property var features: (%(features)s)
    property var doctor: ({running: false, ran: true, ok: true, warnings: true,
        text: "INFO doctor: Target MAC configured\nWARNING doctor: Adapter CoD = 0x6c010c"})
    property var pluginSettings: (%(plugins)s)
    function loadFeatures() {}
    function setFeature(name, enabled) {}
    function runDoctor() {}
    function restartBackend() {}
    function syncContacts() {}
    function loadPlugins() {}
    function loadPluginStore(refresh) {}
    function setPluginEnabled(id, enabled) {}
    function preparePluginInstall(url, ref) {}
    function installStorePlugin(id) {}
    function preparePluginUpdate(id) {}
    function confirmPluginInstall() {}
    function cancelPluginInstall() {}
    function removePlugin(id) {}
    function loadPluginConfig(id) {}
    function savePluginConfig(id, values) {}
    function closePluginConfig() {}
    function setPluginIndexes(urls) {}
    function clearPluginMessage() {}
    function setNotificationPolicy(policy) {}
    function setContactsOnlyNotifications(enabled) {}
    function setMirrorNotificationRemovals(enabled) {}
    function setPhoneAudioRoute(route) {}
    function setProximityLock(enabled, grace) {}
    property var notificationOpenMap: []
    function loadNotificationOpenMap() {}
    function refreshCompanionTools() {}
    function runCompanionTool(action) {}
    function clearCompanionMessage() {}
    signal pairingConfirmationRequested(string passkey)
    signal messageOpenRequested(string handle)
    signal messageSendSucceeded(string recipient, string body)
    signal threadSendSucceeded(string key, string body)
    signal groupConfirmationRequested(string key, string body, string recipients)
    function avatarSource(address) { return ""; }
    function refresh() {}
    function markThreadRead(key) {}
    function watchCallHistory(watched) {}
    function watchNotifications(watched) {}
    function setThreadStarred(key, starred) {}
}
"""


def _render(scheme: str, out_dir: Path) -> None:
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest

    application = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlEngine()
    engine.warnings.connect(
        lambda errors: [print("QML:", e.toString(), file=sys.stderr) for e in errors]
    )
    source = BRIDGE_QML % {
        "status": json.dumps(STATUS), "threads": json.dumps(THREADS),
        "features": json.dumps(FEATURES), "plugins": json.dumps(PLUGINS),
        "call_rows": json.dumps(CALL_ROWS),
    }
    bridge_component = QQmlComponent(engine)
    bridge_component.setData(source.encode(), QUrl())
    bridge = bridge_component.create()
    assert bridge is not None, [e.toString() for e in bridge_component.errors()]
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(MAIN_QML)))
    window = component.createWithInitialProperties({"bridge": bridge})
    assert window is not None, [e.toString() for e in component.errors()]
    window.setProperty("selectedThreadKey", "dpd")
    for width, height in SIZES:
        window.resize(width, height)
        QTest.qWait(300)
        application.processEvents()
        path = out_dir / f"main-{scheme}-{width}x{height}.png"
        assert isinstance(window, QQuickWindow)
        window.grabWindow().save(str(path))
        print(path)
    # Narrow window with the folded card open while the hands-free modem
    # is still connecting: no battery or signal yet.
    status = dict(STATUS, calls_state="connecting", map=False)
    for key in ("phone_battery_level", "phone_battery_percent", "phone_signal_strength"):
        del status[key]
    bridge.setProperty("status", status)
    bridge.setProperty("featureHints", {"phoneStatus": "Calls and battery: connecting to the iPhone…"})
    # Classic link down and parked by the reconnect backoff.
    bridge.setProperty("reconnect", {
        "available": True, "offered": True,
        "hint": "Waiting for the iPhone; next automatic try in 9 min.",
    })
    window.resize(*SIZES[-1])
    window.findChild(QObject, "messagesPage").setProperty("cardOpen", True)
    QTest.qWait(300)
    path = out_dir / f"main-{scheme}-card-connecting.png"
    window.grabWindow().save(str(path))
    print(path)
    _render_photos(bridge, window, scheme, out_dir)
    _render_settings(bridge, window, scheme, out_dir)
    _render_calls(bridge, window, scheme, out_dir)
    window.deleteLater()
    application.processEvents()


def _render_photos(bridge, window, scheme: str, out_dir: Path) -> None:
    """The Photos tab with fake thumbnails, then without a plugin."""
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest

    items = []
    for index, hue in enumerate((10, 60, 120, 200, 260, 320, 30, 170)):
        path = out_dir / f"thumb-{index}.png"
        image = QImage(256, 192, QImage.Format.Format_RGB32)
        image.fill(QColor.fromHsv(hue, 120, 200))
        image.save(str(path))
        items.append({
            "id": f"p{index}", "label": f"2026-10-0{6 - index % 3} 18:2{index}  "
            + ("Video" if index % 4 == 1 else "Photo"),
            "video": index % 4 == 1, "thumbnail": QUrl.fromLocalFile(str(path)).toString(),
            "original": "",
        })
    window.findChild(QObject, "messagesPage").setProperty("cardOpen", False)
    window.resize(*SIZES[0])
    window.setProperty("currentTab", 3)
    bridge.setProperty("photos", {
        "present": True, "ready": True, "loaded": True,
        "hint": "8 recent items from photos.joshuahirsig.xyz", "items": items,
    })
    QTest.qWait(500)
    path = out_dir / f"main-{scheme}-photos.png"
    window.grabWindow().save(str(path))
    print(path)
    bridge.setProperty("photos", {
        "present": False, "ready": False, "loaded": True,
        "hint": "No photo plugin is installed. Add Immich photos in Settings > Plugins, or run: "
        "blueferry plugins install https://github.com/joshii-h/blueferry-plugin-immich",
        "items": [],
    })
    QTest.qWait(300)
    path = out_dir / f"main-{scheme}-photos-missing.png"
    window.grabWindow().save(str(path))
    print(path)


def _render_settings(bridge, window, scheme: str, out_dir: Path) -> None:
    """Every settings category wide, plus the narrow list and drill-down."""
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest

    bridge.setProperty("status", dict(STATUS, storage_policy="encrypted", storage_state="ready",
                                      notification_policy="all"))
    window.resize(*SIZES[0])
    window.setProperty("currentTab", 0)
    window.metaObject().invokeMethod(window, "openPhoneSettings")
    QTest.qWait(400)
    page = window.findChild(QObject, "phoneSettingsPage")
    for category in ("phone", "notifications", "calls", "network", "security",
                     "plugins", "about"):
        page.setProperty("category", category)
        QTest.qWait(400)
        path = out_dir / f"settings-{scheme}-{category}.png"
        window.grabWindow().save(str(path))
        print(path)
    window.resize(*SIZES[-1])
    page.setProperty("drilled", False)
    QTest.qWait(400)
    path = out_dir / f"settings-{scheme}-narrow-list.png"
    window.grabWindow().save(str(path))
    print(path)
    page.setProperty("category", "plugins")
    page.setProperty("drilled", True)
    QTest.qWait(400)
    path = out_dir / f"settings-{scheme}-narrow-plugins.png"
    window.grabWindow().save(str(path))
    print(path)


def _render_calls(bridge, window, scheme: str, out_dir: Path) -> None:
    """Calls tab: a running call, then the hands-free link not ready yet."""
    from datetime import datetime, timedelta, timezone

    from PySide6.QtTest import QTest

    window.metaObject().invokeMethod(window, "closePhoneSettings")
    QTest.qWait(300)
    window.resize(*SIZES[0])
    bridge.setProperty("status", dict(STATUS, call_history_enabled=True))
    bridge.setProperty("callsState", "ready")
    started = (datetime.now(timezone.utc) - timedelta(minutes=3, seconds=12)).isoformat()
    bridge.setProperty("phoneCalls", [{
        "call_id": "voicecall01", "state": "active", "ringing": False,
        "display_peer": "Anna Muster", "number": "+41790000001", "first_seen": started,
    }])
    window.setProperty("currentTab", 1)
    QTest.qWait(500)
    for width, height in SIZES:
        window.resize(width, height)
        QTest.qWait(400)
        path = out_dir / f"calls-{scheme}-active-{width}.png"
        window.grabWindow().save(str(path))
        print(path)
    window.resize(*SIZES[0])
    bridge.setProperty("phoneCalls", [])
    bridge.setProperty("callsState", "connecting")
    QTest.qWait(400)
    path = out_dir / f"calls-{scheme}-not-ready.png"
    window.grabWindow().save(str(path))
    print(path)


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bf-ui")  # nosec B108
    out_dir.mkdir(parents=True, exist_ok=True)
    scheme = os.environ.get("BF_RENDER_SCHEME")
    if scheme:
        _render(scheme, out_dir)
        return
    for name, colors in SCHEMES.items():
        with tempfile.TemporaryDirectory() as config:
            # The KDE platform theme reads the colour scheme from kdeglobals.
            shutil.copy(f"/usr/share/color-schemes/{colors}.colors", Path(config) / "kdeglobals")
            env = dict(
                os.environ, BF_RENDER_SCHEME=name, XDG_CONFIG_HOME=config,
                QT_QPA_PLATFORM="offscreen", QT_QPA_PLATFORMTHEME="kde",
                QT_QUICK_CONTROLS_STYLE="org.kde.desktop",
                PYTHONPATH=str(ROOT / "src"),
            )
            subprocess.run(  # nosec B603 - fixed interpreter and script path
                [sys.executable, __file__, str(out_dir)], env=env, check=True
            )


if __name__ == "__main__":
    main()
