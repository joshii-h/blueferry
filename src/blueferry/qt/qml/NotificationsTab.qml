pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

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

    Kirigami.InlineMessage {
        objectName: "notificationContentHint"
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.smallSpacing
        visible: tab.info.enabled === true && tab.info.content !== true
        type: Kirigami.MessageType.Information
        text: qsTr("Only app names and times are shown. Set BLUEFERRY_SHOW_NOTIFICATION_CONTENT=true in local.env to see titles and text.")
    }

    Controls.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true

        ListView {
            id: notificationList
            model: tab.rows
            clip: true

            delegate: Controls.ItemDelegate {
                id: row
                required property var modelData
                width: ListView.view.width
                Accessible.name: row.modelData.app + ", " + row.modelData.time
                contentItem: ColumnLayout {
                    spacing: 0
                    RowLayout {
                        Layout.fillWidth: true
                        Controls.Label {
                            Layout.fillWidth: true
                            text: row.modelData.app
                            textFormat: Text.PlainText
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Controls.Label {
                            text: row.modelData.time
                            textFormat: Text.PlainText
                            opacity: 0.7
                        }
                    }
                    Controls.Label {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: [row.modelData.title, row.modelData.subtitle]
                            .filter(part => part !== "").join(" · ")
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                    }
                    Controls.Label {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: row.modelData.body
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        maximumLineCount: 4
                        elide: Text.ElideRight
                        opacity: 0.8
                    }
                }
            }

            Kirigami.PlaceholderMessage {
                objectName: "notificationsPlaceholder"
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit * 4
                visible: notificationList.count === 0
                enabled: tab.optInHint === ""
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
