pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Dialogs as Dialogs
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "ui"

// Phone card section "Tools": UxPlay screen mirroring, LocalSend and the
// iPhone's camera roll over USB. These are local programs started by this
// client, not daemon features. Tools that are not installed stay visible but
// disabled, with what to install. All texts come from the bridge.
ColumnLayout {
    id: section
    objectName: "companionTools"
    required property var bridge

    readonly property var tools: section.bridge.companionTools || ({})
    readonly property var rows: (section.tools.tools || []).filter(tool => tool.key !== "eject")
    readonly property bool busy: (section.tools.busy || "") !== ""
    // "Send to…" targets from plugins with the share capability.
    readonly property var surfaces: section.bridge.pluginSurfaces || ({})
    readonly property var shareTargets: section.surfaces.targets || []
    property string sendKey: ""

    function loadTargets() {
        if (typeof section.bridge.loadShareTargets === "function")
            section.bridge.loadShareTargets()
    }

    function tool(key) {
        return (section.tools.tools || []).find(entry => entry.key === key) || ({})
    }
    // The row delegate for ``key`` (tests and keyboard navigation).
    function rowFor(key) {
        for (let index = 0; index < rowRepeater.count; ++index) {
            const item = rowRepeater.itemAt(index)
            if (item && item.objectName === "companionTool_" + key)
                return item
        }
        return null
    }
    function refresh() {
        if (typeof section.bridge.refreshCompanionTools === "function")
            section.bridge.refreshCompanionTools()
    }
    function iconFor(key) {
        if (key === "mirror")
            return "video-display"
        if (key === "send")
            return "document-send"
        return "folder-pictures"
    }

    Layout.fillWidth: true
    spacing: 0

    SectionHeader {
        text: qsTr("Tools")
        level: 4
    }

    Repeater {
        id: rowRepeater
        model: section.rows
        delegate: ListRow {
            id: row
            required property var modelData
            readonly property bool running: section.tools.busy === row.modelData.key
            readonly property alias ejectButton: ejectButton
            readonly property bool ejectable: row.modelData.key === "photos"
                && section.tool("eject").enabled === true

            objectName: "companionTool_" + row.modelData.key
            Layout.fillWidth: true
            density: "compact"
            wrapSubtitle: true
            avatarSize: Kirigami.Units.iconSizes.smallMedium
            title: row.modelData.title
            subtitle: row.modelData.subtitle
            iconName: row.modelData.key === "mirror" && row.modelData.active
                ? "media-playback-stop" : section.iconFor(row.modelData.key)
            enabled: row.modelData.enabled === true && !section.busy
            pinActions: row.running || row.ejectable
            Controls.ToolTip.text: row.modelData.subtitle
            Controls.ToolTip.visible: row.hovered && !row.modelData.installed
            Controls.ToolTip.delay: Kirigami.Units.toolTipDelay
            onClicked: section.bridge.runCompanionTool(row.modelData.key)

            Controls.BusyIndicator {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                visible: row.running
                running: row.running
            }
            Controls.ToolButton {
                id: ejectButton
                objectName: "companionEjectButton"
                visible: row.ejectable
                enabled: !section.busy
                icon.name: "media-eject"
                text: qsTr("Eject")
                display: Controls.AbstractButton.IconOnly
                Controls.ToolTip.text: section.tool("eject").title || text
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.delay: Kirigami.Units.toolTipDelay
                onClicked: section.bridge.runCompanionTool("eject")
            }
        }
    }

    ListRow {
        id: sendRow
        objectName: "sendToRow"
        Layout.fillWidth: true
        visible: section.shareTargets.length > 0
        density: "compact"
        wrapSubtitle: true
        avatarSize: Kirigami.Units.iconSizes.smallMedium
        iconName: "document-send"
        title: qsTr("Send to…")
        subtitle: section.shareTargets.map(target => target.label).join(", ")
        enabled: (section.surfaces.busy || "") === ""
        onClicked: sendMenu.popup(sendRow, 0, sendRow.height)

        Controls.Menu {
            id: sendMenu
            objectName: "sendToMenu"
            onAboutToShow: section.loadTargets()
            Repeater {
                model: section.shareTargets
                delegate: Controls.MenuItem {
                    required property var modelData
                    text: modelData.label
                    icon.name: modelData.icon
                    onTriggered: {
                        section.sendKey = modelData.key
                        sendDialog.open()
                    }
                }
            }
        }
    }

    Dialogs.FileDialog {
        id: sendDialog
        title: qsTr("Choose Files to Send")
        fileMode: Dialogs.FileDialog.OpenFiles
        onAccepted: section.bridge.sendToTarget(section.sendKey, selectedFiles)
    }

    Kirigami.InlineMessage {
        objectName: "companionMessage"
        Layout.fillWidth: true
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        Layout.topMargin: Kirigami.Units.smallSpacing
        visible: (section.tools.message || "") !== ""
        type: section.tools.messageOk === false
            ? Kirigami.MessageType.Warning : Kirigami.MessageType.Positive
        text: section.tools.message || ""
        showCloseButton: true
        // The close button sets visible = false; clear the message and
        // restore the binding so the next message shows again.
        onVisibleChanged: {
            if (visible)
                return
            if ((section.tools.message || "") !== ""
                    && typeof section.bridge.clearCompanionMessage === "function")
                section.bridge.clearCompanionMessage()
            visible = Qt.binding(function() { return (section.tools.message || "") !== "" })
        }
        actions: [
            Kirigami.Action {
                text: qsTr("Ask the iPhone again")
                icon.name: "emblem-locked"
                visible: section.tools.needsPairing === true
                enabled: !section.busy
                onTriggered: section.bridge.runCompanionTool("pair")
            }
        ]
    }

    // USB devices come and go without a signal; look again while shown.
    Timer {
        interval: 5000
        repeat: true
        running: section.visible && !section.busy
        onTriggered: section.refresh()
    }
    Component.onCompleted: {
        section.refresh()
        section.loadTargets()
    }
}
