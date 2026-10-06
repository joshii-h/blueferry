pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Fixed overview of the paired iPhone on the left of the main window: name,
// battery/signal, now playing and quick switches. Opt-in features that are
// off stay visible but disabled, with the local.env setting that enables
// them. Everything goes through the bridge; no I/O happens here.
Controls.ScrollView {
    id: card
    objectName: "phoneCard"
    required property var bridge

    readonly property var status: card.bridge.status || ({})
    readonly property var hints: card.bridge.featureHints || ({})
    readonly property var audio: card.bridge.phoneAudio || ({})
    readonly property var nowPlaying: card.bridge.nowPlaying || ({})
    readonly property var tether: card.bridge.tether || ({})
    readonly property bool connected: card.status.map === true
    readonly property bool hasPhoneStatus: typeof card.status.phone_battery_level === "number"
        || typeof card.status.phone_battery_percent === "number"
        || typeof card.status.phone_signal_strength === "number"

    function connectionText() {
        if (card.status.daemon !== true)
            return qsTr("BlueFerry service is not running")
        if (card.status.initializing === true)
            return qsTr("Connecting…")
        return card.connected ? qsTr("Connected") : qsTr("Not connected")
    }

    contentWidth: availableWidth
    Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

    ColumnLayout {
        width: card.availableWidth
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            Kirigami.Icon {
                Layout.alignment: Qt.AlignTop
                source: "smartphone"
                Layout.preferredWidth: Kirigami.Units.iconSizes.large
                Layout.preferredHeight: Kirigami.Units.iconSizes.large
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing / 2
                Kirigami.Heading {
                    objectName: "phoneCardName"
                    Layout.fillWidth: true
                    level: 3
                    text: card.bridge.phoneName || "iPhone"
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }
                Controls.Label {
                    objectName: "phoneCardConnection"
                    Layout.fillWidth: true
                    text: card.connectionText()
                    textFormat: Text.PlainText
                    color: Kirigami.Theme.disabledTextColor
                    elide: Text.ElideRight
                }
                // Battery, signal and network from the optional HFP
                // integration; the indicator only exists while a value is known.
                Loader {
                    id: phoneStatusLoader
                    Layout.fillWidth: true
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    active: card.hasPhoneStatus
                    visible: active
                    sourceComponent: PhoneStatusIndicator {
                        objectName: "phoneStatusIndicator"
                        status: card.status
                    }
                }
                Controls.Label {
                    objectName: "phoneStatusHint"
                    Layout.fillWidth: true
                    visible: !card.hasPhoneStatus && (card.hints.phoneStatus || "") !== ""
                    text: card.hints.phoneStatus || ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Kirigami.Theme.disabledTextColor
                    font: Kirigami.Theme.smallFont
                }
            }
        }

        Kirigami.Separator { Layout.fillWidth: true }

        Kirigami.Heading {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.largeSpacing
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            level: 4
            text: qsTr("Now Playing")
        }
        Loader {
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.smallSpacing
            Layout.topMargin: Kirigami.Units.smallSpacing
            Layout.bottomMargin: Kirigami.Units.smallSpacing
            active: card.nowPlaying.available === true
            visible: active
            sourceComponent: NowPlayingBar {
                nowPlaying: card.nowPlaying
                onCommandRequested: command => card.bridge.sendMediaCommand(command)
            }
        }
        Controls.Label {
            objectName: "nowPlayingHint"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            Layout.topMargin: Kirigami.Units.smallSpacing
            visible: card.nowPlaying.available !== true
            text: card.hints.media || qsTr("Waiting for the iPhone's media service.")
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Kirigami.Theme.disabledTextColor
            font: Kirigami.Theme.smallFont
        }

        Kirigami.Separator { Layout.fillWidth: true; Layout.topMargin: Kirigami.Units.smallSpacing }

        Kirigami.Heading {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.largeSpacing
            Layout.bottomMargin: Kirigami.Units.smallSpacing
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            level: 4
            text: qsTr("Quick Settings")
        }

        SubtitleSwitch {
            id: audioSwitch
            objectName: "phoneAudioSwitch"
            Layout.fillWidth: true
            text: qsTr("Sound on this computer")
            subtitle: card.audio.hint || ""
            checked: card.audio.onPc === true
            enabled: card.status.daemon === true && card.audio.available === true
                && card.audio.pending !== true
            onToggled: {
                card.bridge.setPhoneAudioRoute(checked ? "pc" : "phone")
                // Follow the daemon's report, not the click.
                checked = Qt.binding(function() { return card.audio.onPc === true })
            }
        }

        Loader {
            Layout.fillWidth: true
            active: card.tether.available === true
            visible: active
            sourceComponent: TetherSection {
                bridge: card.bridge
                compact: true
            }
        }
        SubtitleSwitch {
            objectName: "tetherUnavailableSwitch"
            Layout.fillWidth: true
            visible: card.tether.available !== true
            enabled: false
            text: qsTr("Hotspot")
            subtitle: qsTr("Internet sharing is not offered by the running BlueFerry service.")
        }

        SubtitleSwitch {
            id: proximitySwitch
            objectName: "proximitySwitch"
            Layout.fillWidth: true
            Layout.bottomMargin: Kirigami.Units.largeSpacing
            text: qsTr("Lock when I walk away")
            subtitle: card.hints.proximity || ""
            checked: card.status.proximity_lock_enabled === true
            enabled: card.status.daemon === true && card.status.proximity_lock !== undefined
                && !card.bridge.busy
            onToggled: {
                card.bridge.setProximityLock(checked, card.status.proximity_lock_grace_sec || 60)
                checked = Qt.binding(function() { return card.status.proximity_lock_enabled === true })
            }
        }
    }
}
