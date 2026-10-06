pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Settings > Network: the iPhone's Personal Hotspot over Bluetooth.
ColumnLayout {
    id: section
    objectName: "settingsNetwork"
    required property var bridge
    spacing: Kirigami.Units.largeSpacing

    // Loaded only when the running daemon offers Tether1.
    Loader {
        objectName: "tetherLoader"
        Layout.fillWidth: true
        active: section.bridge.tether !== undefined
            && section.bridge.tether.available === true
        visible: active
        sourceComponent: Component {
            TetherSection { bridge: section.bridge }
        }
    }
    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: section.bridge.tether === undefined || section.bridge.tether.available !== true
        type: Kirigami.MessageType.Information
        text: qsTr("Internet sharing is not offered by the running BlueFerry service.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "tether_autoconnect"
        variable: "BLUEFERRY_TETHER_AUTOCONNECT"
        text: qsTr("Connect the hotspot automatically")
        description: qsTr("Joins the iPhone's Personal Hotspot whenever the iPhone connects.")
    }
}
