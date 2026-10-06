pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Settings > Notifications: popup policy, content, following the iPhone's
// list, ANCS action buttons, history and click rules.
ColumnLayout {
    id: section
    objectName: "settingsNotifications"
    required property var bridge
    spacing: Kirigami.Units.largeSpacing

    Kirigami.Heading { text: qsTr("Desktop Notifications"); level: 2 }
    Controls.Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: qsTr("Choose which iPhone events create desktop popups. Messages only is the default.")
    }
    Kirigami.FormLayout {
        Layout.fillWidth: true

        Controls.ComboBox {
            Kirigami.FormData.label: qsTr("Show Popups:")
            textRole: "text"
            valueRole: "value"
            model: [
                { "text": qsTr("All iPhone Notifications"), "value": "all" },
                { "text": qsTr("Messages Only"), "value": "messages" },
                { "text": qsTr("None"), "value": "none" }
            ]
            currentIndex: section.bridge.status.notification_policy === "all" ? 0
                : section.bridge.status.notification_policy === "none" ? 2 : 1
            enabled: section.bridge.status.daemon === true && !section.bridge.busy
            onActivated: section.bridge.setNotificationPolicy(currentValue)
        }
        Controls.CheckBox {
            Layout.fillWidth: true
            text: qsTr("Only notify for contacts")
            checked: section.bridge.status.contacts_only_notifications === true
            enabled: section.bridge.status.daemon === true
                && section.bridge.status.notification_policy !== "none"
                && !section.bridge.busy
            onClicked: section.bridge.setContactsOnlyNotifications(checked)
            Accessible.description: qsTr("Unknown senders remain available in message history.")
        }
    }
    // Click rules only apply to non-Messages popups, which exist only in
    // the "all" policy. Load the editor (and its backend read) on demand.
    Loader {
        objectName: "notificationOpenMapLoader"
        Layout.fillWidth: true
        active: section.bridge.status.daemon === true
            && section.bridge.status.notification_policy === "all"
        visible: active
        sourceComponent: Component {
            NotificationOpenMapEditor {
                bridge: section.bridge
            }
        }
    }

    Kirigami.Heading { text: qsTr("Content and Actions"); level: 2 }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "show_notification_content"
        variable: "BLUEFERRY_SHOW_NOTIFICATION_CONTENT"
        text: qsTr("Show message content in popups")
        description: qsTr("Off: popups name the sender only.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "ancs_actions"
        variable: "BLUEFERRY_ANCS_ACTIONS"
        text: qsTr("iPhone action buttons")
        description: qsTr("Offers the iPhone's actions (Accept, Decline, Clear…) on other apps' popups. Needs message content.")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "mark_read_on_dismiss"
        variable: "BLUEFERRY_MARK_READ_ON_DISMISS"
        text: qsTr("Mark read when a popup is dismissed")
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "otp_autocopy"
        variable: "BLUEFERRY_OTP_AUTOCOPY"
        text: qsTr("Copy one-time codes")
        description: qsTr("Puts verification codes from messages on the clipboard and clears them again.")
    }

    Kirigami.Heading { text: qsTr("Sync with iPhone"); level: 2 }
    SubtitleSwitch {
        objectName: "settingsMirrorSwitch"
        Layout.fillWidth: true
        horizontalPadding: 0
        text: qsTr("Sync notifications with iPhone")
        subtitle: section.bridge.status.mirror_iphone_removals === undefined
            ? qsTr("Not offered by the running BlueFerry service.")
            : checked
                ? qsTr("Notifications removed on the iPhone also disappear here.")
                : qsTr("The list keeps notifications removed on the iPhone.")
        checked: section.bridge.status.mirror_iphone_removals === true
        enabled: section.bridge.status.daemon === true
            && section.bridge.status.mirror_iphone_removals !== undefined
            && !section.bridge.busy
        onToggled: {
            section.bridge.setMirrorNotificationRemovals(checked)
            checked = Qt.binding(function() { return section.bridge.status.mirror_iphone_removals === true })
        }
    }
    FeatureSwitch {
        Layout.fillWidth: true
        bridge: section.bridge
        feature: "notification_history"
        variable: "BLUEFERRY_NOTIFICATION_HISTORY"
        text: qsTr("Keep recent iPhone notifications")
        description: qsTr("Lists other apps' notifications in the Notifications tab (memory only).")
    }
}
