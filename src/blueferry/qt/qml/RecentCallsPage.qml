pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Opt-in list of the iPhone's recent calls, embedded in the Calls tab. Main
// only instantiates it through a Loader when the backend reports
// call_history_enabled, and records are fetched only while the tab is shown.
// Rows come pre-grouped from the controller (phone_overview.call_groups):
// one section per day, repeated calls from one number folded with a count.
ColumnLayout {
    id: callsPage
    objectName: "recentCallsPage"
    required property var bridge
    property bool missedOnly: false
    readonly property bool canCall: (callsPage.bridge.status || ({})).calls_enabled === true
        && callsPage.bridge.callsState === "ready"
    signal callRequested(string number)
    signal messageRequested(string address)
    spacing: 0

    function visibleCalls() {
        const rows = callsPage.bridge.callHistoryRows || ({})
        return (callsPage.missedOnly ? rows.missed : rows.all) || []
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
        Layout.margins: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.smallSpacing
        Kirigami.Heading {
            Layout.fillWidth: true
            level: 2
            text: qsTr("Recent Calls")
        }
        // Segmented filter: All | Missed.
        Row {
            objectName: "callsFilter"
            spacing: 0
            Controls.Button {
                objectName: "callsFilterAll"
                text: qsTr("All")
                checkable: true
                checked: !callsPage.missedOnly
                autoExclusive: true
                onClicked: callsPage.missedOnly = false
            }
            Controls.Button {
                objectName: "callsFilterMissed"
                text: qsTr("Missed")
                checkable: true
                checked: callsPage.missedOnly
                autoExclusive: true
                onClicked: callsPage.missedOnly = true
            }
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
            section.property: "day"
            section.criteria: ViewSection.FullString
            section.delegate: Kirigami.ListSectionHeader {
                required property string section
                width: ListView.view.width
                text: section
            }

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
                    + ", " + callRow.modelData.caller + ", " + callRow.modelData.clock

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Item {
                        Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                        Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                        ContactAvatar {
                            anchors.fill: parent
                            bridge: callsPage.bridge
                            address: callRow.modelData.address || ""
                        }
                        // Direction as a small badge on the avatar.
                        Rectangle {
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: -2
                            width: Kirigami.Units.iconSizes.small
                            height: width
                            radius: width / 2
                            color: Kirigami.Theme.backgroundColor
                            Kirigami.Icon {
                                anchors.fill: parent
                                anchors.margins: 1
                                source: callsPage.directionIcon(callRow.modelData.direction)
                                color: callRow.modelData.missed ? Kirigami.Theme.negativeTextColor
                                                                : Kirigami.Theme.textColor
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        Controls.Label {
                            objectName: "callCaller"
                            Layout.fillWidth: true
                            // Remote names are plain text, never markup.
                            textFormat: Text.PlainText
                            text: callRow.modelData.count > 1
                                ? callRow.modelData.caller + " (" + callRow.modelData.count + ")"
                                : callRow.modelData.caller
                            elide: Text.ElideRight
                            color: callRow.modelData.missed ? Kirigami.Theme.negativeTextColor
                                                            : Kirigami.Theme.textColor
                        }
                        Controls.Label {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            text: callRow.modelData.known && callRow.modelData.address !== ""
                                ? callsPage.directionText(callRow.modelData.direction) + " · "
                                    + callRow.modelData.address
                                : callsPage.directionText(callRow.modelData.direction)
                            elide: Text.ElideRight
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                        }
                    }

                    Controls.ToolButton {
                        objectName: "callBackButton"
                        visible: callRow.hovered && callRow.modelData.address !== ""
                        icon.name: "call-start"
                        text: qsTr("Call back")
                        display: Controls.AbstractButton.IconOnly
                        enabled: callsPage.canCall && !callsPage.bridge.busy
                        Controls.ToolTip.text: callsPage.canCall ? text
                            : qsTr("Calls are not ready.")
                        Controls.ToolTip.visible: hovered
                        onClicked: callsPage.callRequested(callRow.modelData.address)
                    }
                    Controls.ToolButton {
                        objectName: "callMessageButton"
                        visible: callRow.hovered && callRow.modelData.address !== ""
                        icon.name: "dialog-messages"
                        text: qsTr("Send a message")
                        display: Controls.AbstractButton.IconOnly
                        Controls.ToolTip.text: text
                        Controls.ToolTip.visible: hovered
                        onClicked: callsPage.messageRequested(callRow.modelData.address)
                    }

                    Controls.Label {
                        textFormat: Text.PlainText
                        text: callRow.modelData.clock
                        opacity: 0.7
                    }
                }
            }
        }
    }
}
