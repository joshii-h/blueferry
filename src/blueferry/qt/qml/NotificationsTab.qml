pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "ui"

// Recent iPhone app notifications (opt-in, kept only in the daemon's
// memory). Main asks the bridge to fetch them only while this tab is shown.
// Every field comes from the iPhone and is rendered as plain text.
ColumnLayout {
    id: tab
    objectName: "notificationsTab"
    required property var bridge
    readonly property var info: tab.bridge.notificationsInfo || ({})
    readonly property string optInHint: (tab.bridge.featureHints || {}).notifications || ""
    readonly property var rows: tab.bridge.notifications || []
    spacing: 0

    SectionHeader {
        text: qsTr("Notifications")
        Controls.ToolButton {
            objectName: "notificationsRefresh"
            icon.name: "view-refresh"
            text: qsTr("Refresh")
            display: Controls.AbstractButton.IconOnly
            enabled: tab.optInHint === ""
            Accessible.name: text
            Controls.ToolTip.text: text
            Controls.ToolTip.visible: hovered
            onClicked: tab.bridge.refreshNotifications()
        }
    }

    Kirigami.InlineMessage {
        objectName: "notificationContentHint"
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.smallSpacing
        visible: tab.info.enabled === true && tab.info.content !== true
        type: Kirigami.MessageType.Information
        text: qsTr("Only app names and times are shown. Turn on “Show message content in popups” in Settings › Notifications to see titles and text.")
    }

    // Pin results and the pin limit; texts from notification_pin_view.
    Kirigami.InlineMessage {
        objectName: "notificationPinError"
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.smallSpacing
        visible: (tab.info.pinError || "") !== ""
        type: Kirigami.MessageType.Error
        text: tab.info.pinError || ""
    }
    Kirigami.InlineMessage {
        objectName: "notificationPinLimit"
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.smallSpacing
        visible: tab.info.pinLimitReached === true && (tab.info.pinLimitText || "") !== ""
        type: Kirigami.MessageType.Information
        text: tab.info.pinLimitText || ""
    }

    Controls.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

        ListView {
            id: notificationList
            model: tab.rows
            clip: true

            delegate: ListRow {
                id: row
                required property var modelData
                readonly property bool pinned: row.modelData.pinned === true
                partPrefix: "notification"
                iconName: "preferences-desktop-notification"
                // Pinned rows carry a pin badge; removed and muted ones are dimmed.
                badgeIcon: row.pinned ? "pin" : ""
                dimmed: row.modelData.removed === true || row.modelData.muted === true
                pinActions: row.pinned
                title: row.modelData.app
                bold: true
                meta: row.modelData.time
                subtitle: [row.modelData.removed_text || "", row.modelData.title,
                           row.modelData.subtitle || ""]
                    .filter(part => part !== "").join(" · ")
                body: row.modelData.body
                Accessible.name: [row.modelData.app, row.modelData.time,
                                  row.modelData.pinned_text || "", row.modelData.removed_text || ""]
                    .filter(part => part !== "").join(", ")

                Controls.ToolButton {
                    objectName: "notificationPin"
                    visible: row.modelData.can_pin === true
                    icon.name: row.pinned ? "window-unpin" : "window-pin"
                    text: row.modelData.pin_label || ""
                    display: Controls.AbstractButton.IconOnly
                    // Unpinning always works; pinning stops at the limit.
                    enabled: row.pinned || tab.info.pinLimitReached !== true
                    Accessible.name: text
                    Accessible.description: row.modelData.pin_tooltip || ""
                    Controls.ToolTip.text: row.modelData.pin_tooltip || text
                    Controls.ToolTip.visible: hovered
                    onClicked: tab.bridge.setNotificationPinned(row.modelData.key, !row.pinned)
                }
            }

            EmptyState {
                objectName: "notificationsPlaceholder"
                anchors.centerIn: parent
                visible: notificationList.count === 0
                dimmed: tab.optInHint !== ""
                icon.name: "notifications"
                text: tab.optInHint !== ""
                    ? qsTr("Notification List Is Off")
                    : tab.info.error
                        ? qsTr("Notifications Are Unavailable")
                        : qsTr("No Notifications Yet")
                explanation: tab.optInHint !== ""
                    ? tab.optInHint
                    : tab.info.error || qsTr("iPhone app notifications appear here while BlueFerry is running. The list is not saved.")
            }
        }
    }
}
