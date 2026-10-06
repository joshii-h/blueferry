pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Opt-in list of the iPhone's recent calls, embedded in the Calls tab. Main
// only instantiates it through a Loader when the backend reports
// call_history_enabled, and records are fetched only while the tab is shown.
ColumnLayout {
    id: callsPage
    objectName: "recentCallsPage"
    required property var bridge
    property bool missedOnly: false
    spacing: 0

    function visibleCalls() {
        const calls = callsPage.bridge.callHistory || []
        if (!callsPage.missedOnly)
            return calls
        return calls.filter(call => call.missed === true)
    }

    function directionText(direction) {
        if (direction === "missed")
            return qsTr("Missed")
        if (direction === "outgoing")
            return qsTr("Outgoing")
        return qsTr("Incoming")
    }

    function directionIcon(direction) {
        if (direction === "missed")
            return "call-missed"
        if (direction === "outgoing")
            return "call-outgoing"
        return "call-incoming"
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.smallSpacing
        Kirigami.Heading {
            Layout.fillWidth: true
            level: 3
            text: qsTr("Recent Calls")
        }
        Controls.CheckBox {
            objectName: "missedOnlyCheckBox"
            text: qsTr("Missed Only")
            checked: callsPage.missedOnly
            onToggled: callsPage.missedOnly = checked
        }
        Controls.ToolButton {
            icon.name: "view-refresh"
            text: qsTr("Refresh from iPhone")
            display: Controls.AbstractButton.IconOnly
            enabled: !callsPage.bridge.busy
            Accessible.name: text
            Controls.ToolTip.text: text
            Controls.ToolTip.visible: hovered
            onClicked: callsPage.bridge.syncCallHistory()
        }
    }

    Controls.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true

        ListView {
            id: callsList
            model: callsPage.visibleCalls()
            reuseItems: true

            Kirigami.PlaceholderMessage {
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit * 4
                visible: callsList.count === 0
                icon.name: callsPage.bridge.callHistoryError !== "" ? "dialog-warning" : "call-start"
                text: callsPage.bridge.callHistoryError !== ""
                    ? qsTr("Call history is unavailable")
                    : (callsPage.missedOnly ? qsTr("No missed calls") : qsTr("No recent calls"))
                explanation: callsPage.bridge.callHistoryError
            }

            delegate: Controls.ItemDelegate {
                id: callRow
                required property var modelData
                width: ListView.view.width
                Accessible.name: callsPage.directionText(callRow.modelData.direction)
                    + ", " + callRow.modelData.caller + ", " + callRow.modelData.time

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Icon {
                        source: callsPage.directionIcon(callRow.modelData.direction)
                        color: callRow.modelData.missed ? Kirigami.Theme.negativeTextColor
                                                        : Kirigami.Theme.textColor
                        Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                        Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        Controls.Label {
                            Layout.fillWidth: true
                            // Remote names are plain text, never markup.
                            textFormat: Text.PlainText
                            text: callRow.modelData.caller
                            elide: Text.ElideRight
                            color: callRow.modelData.missed ? Kirigami.Theme.negativeTextColor
                                                            : Kirigami.Theme.textColor
                        }
                        Controls.Label {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            text: callRow.modelData.name !== "" && callRow.modelData.address !== ""
                                ? callsPage.directionText(callRow.modelData.direction) + " · "
                                    + callRow.modelData.address
                                : callsPage.directionText(callRow.modelData.direction)
                            elide: Text.ElideRight
                            opacity: 0.7
                        }
                    }

                    Controls.Label {
                        textFormat: Text.PlainText
                        text: callRow.modelData.time
                        opacity: 0.7
                    }
                }
            }
        }
    }
}
