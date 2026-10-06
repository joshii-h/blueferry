pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Settings > Calls & Audio: hands-free calls, call history, missed-call
// popups, where the iPhone's sound plays, media control.
ColumnLayout {
    id: section
    objectName: "settingsCallsAudio"
    required property var bridge
    readonly property var audio: section.bridge.phoneAudio || ({})
    spacing: Kirigami.Units.largeSpacing

    Kirigami.Heading { text: qsTr("Calls"); level: 2 }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "calls_enabled"
        variable: "BLUEFERRY_CALLS_ENABLED"
        text: qsTr("Phone calls on this computer (experimental)")
        description: qsTr("Answer and place iPhone calls through oFono; also reports battery and signal.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "call_history_enabled"
        variable: "BLUEFERRY_CALL_HISTORY_ENABLED"
        text: qsTr("Recent calls")
        description: qsTr("Reads the iPhone's call list for the Calls tab.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "missed_call_notifications"
        variable: "BLUEFERRY_MISSED_CALL_NOTIFICATIONS"
        text: qsTr("Missed-call popups")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "phone_battery_notify"
        variable: "BLUEFERRY_PHONE_BATTERY_NOTIFY"
        text: qsTr("Warn when the iPhone's battery is low")
    }

    Kirigami.Heading { text: qsTr("Sound"); level: 2 }
    SubtitleSwitch {
        objectName: "settingsAudioSwitch"
        Layout.fillWidth: true
        horizontalPadding: 0
        text: qsTr("Sound on this computer")
        subtitle: section.audio.hint || ""
        checked: section.audio.onPc === true
        enabled: section.bridge.status.daemon === true && section.audio.available === true
            && section.audio.pending !== true
        onToggled: {
            section.bridge.setPhoneAudioRoute(checked ? "pc" : "phone")
            checked = Qt.binding(function() { return section.audio.onPc === true })
        }
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "keep_phone_audio_on_phone"
        variable: "BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE"
        text: qsTr("Keep iPhone sound on the iPhone")
        description: qsTr("On: this computer never becomes the iPhone's speaker. Turn off to allow the switch above.")
    }

    Kirigami.Heading { text: qsTr("Media"); level: 2 }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "media_control_enabled"
        variable: "BLUEFERRY_MEDIA_CONTROL_ENABLED"
        text: qsTr("Now playing and media buttons")
        description: qsTr("Shows what the iPhone plays and controls it.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "media_mpris_enabled"
        variable: "BLUEFERRY_MEDIA_MPRIS_ENABLED"
        text: qsTr("Desktop media controls (MPRIS)")
        description: qsTr("Every app in your session can then read the track title.")
    }
}
