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
    property bool callHistoryEnabled: false
    property var callHistory: []
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
    source = BRIDGE_QML % {"status": json.dumps(STATUS), "threads": json.dumps(THREADS)}
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
        "hint": "No photo plugin is installed. Set up Immich photos with: "
        "blueferry plugins immich setup --url https://your-immich-server",
        "items": [],
    })
    QTest.qWait(300)
    path = out_dir / f"main-{scheme}-photos-missing.png"
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
