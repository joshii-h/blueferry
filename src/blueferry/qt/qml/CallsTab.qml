pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Calls tab: a dial pad for the optional HFP integration and the optional
// recent-calls list. Both stay visible but disabled when their opt-in is off.
GridLayout {
    id: callsTab
    objectName: "callsTab"
    required property var bridge
    signal activeCallsRequested()

    readonly property var hints: callsTab.bridge.featureHints || ({})
    readonly property bool callsEnabled: (callsTab.bridge.status || {}).calls_enabled === true
    readonly property bool callsReady: callsTab.bridge.callsState === "ready"
    readonly property int activeCalls: (callsTab.bridge.phoneCalls || []).length

    // HFP dialing accepts digits, +, * and # only.
    function dialable(value: string): string {
        return String(value || "").replace(/[^0-9+*#]/g, "").slice(0, 32)
    }

    function append(key: string): void {
        dialField.text = callsTab.dialable(dialField.text + key)
    }

    function dial(): void {
        const number = callsTab.dialable(dialField.text)
        if (number !== "" && callsTab.callsReady)
            callsTab.bridge.dialCall(number)
    }

    columns: width > Kirigami.Units.gridUnit * 34 ? 2 : 1
    columnSpacing: 0
    rowSpacing: 0

    ColumnLayout {
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: Kirigami.Units.gridUnit * 14
        Layout.fillWidth: callsTab.columns === 1
        Layout.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.smallSpacing
        enabled: callsTab.callsEnabled

        Kirigami.Heading { level: 3; text: qsTr("Dial") }

        Controls.TextField {
            id: dialField
            objectName: "dialPadField"
            Layout.fillWidth: true
            placeholderText: qsTr("Phone number")
            inputMethodHints: Qt.ImhDialableCharactersOnly
            validator: RegularExpressionValidator { regularExpression: /[0-9+*#]{0,32}/ }
            Accessible.name: qsTr("Phone number")
            onAccepted: callsTab.dial()
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: Kirigami.Units.smallSpacing
            rowSpacing: Kirigami.Units.smallSpacing

            Repeater {
                model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]
                delegate: Controls.Button {
                    required property string modelData
                    objectName: "dialKey" + modelData
                    Layout.fillWidth: true
                    text: modelData
                    Accessible.name: qsTr("Dial %1").arg(modelData)
                    onClicked: callsTab.append(modelData)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Controls.Button {
                objectName: "dialPlusKey"
                text: "+"
                Accessible.name: qsTr("Dial +")
                onClicked: callsTab.append("+")
            }
            Controls.Button {
                icon.name: "edit-clear"
                text: qsTr("Delete")
                display: Controls.AbstractButton.IconOnly
                Accessible.name: text
                Controls.ToolTip.text: text
                Controls.ToolTip.visible: hovered
                enabled: dialField.text !== ""
                onClicked: dialField.text = dialField.text.slice(0, -1)
            }
            Item { Layout.fillWidth: true }
            Controls.Button {
                objectName: "dialPadCallButton"
                text: qsTr("Call")
                icon.name: "call-start"
                enabled: callsTab.callsReady && dialField.text !== "" && !callsTab.bridge.busy
                onClicked: callsTab.dial()
            }
        }

        Controls.Button {
            objectName: "activeCallsButton"
            Layout.fillWidth: true
            text: callsTab.activeCalls > 0
                ? qsTr("Active Calls (%1)").arg(callsTab.activeCalls) : qsTr("Active Calls")
            icon.name: "call-start"
            onClicked: callsTab.activeCallsRequested()
        }

        Controls.Label {
            objectName: "callsHint"
            Layout.fillWidth: true
            visible: !callsTab.callsEnabled || !callsTab.callsReady
            text: callsTab.callsEnabled
                ? qsTr("Waiting for the iPhone's hands-free connection.")
                : callsTab.hints.calls || ""
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Kirigami.Theme.disabledTextColor
            font: Kirigami.Theme.smallFont
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 0

        Loader {
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: callsTab.bridge.callHistoryEnabled === true
            visible: active
            sourceComponent: RecentCallsPage { bridge: callsTab.bridge }
        }

        Kirigami.PlaceholderMessage {
            objectName: "callHistoryHint"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: callsTab.bridge.callHistoryEnabled !== true
            enabled: false
            icon.name: "call-start"
            text: qsTr("Recent Calls Are Off")
            explanation: callsTab.hints.callHistory || ""
        }
    }
}
