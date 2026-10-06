pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "ui"

// Phone card section "From Plugins" (PLUGINS.md, capability card): the items
// every enabled card plugin offers, each with up to three action buttons.
// A plugin that crashed or answered garbage shows a dimmed hint instead.
// Everything here is plain text; calls run in the controller's plugin pool.
ColumnLayout {
    id: section
    objectName: "pluginCardSection"
    required property var bridge

    readonly property var surfaces: section.bridge.pluginSurfaces || ({})
    readonly property var cards: section.surfaces.cards || []
    readonly property bool working: (section.surfaces.busy || "") !== ""

    Layout.fillWidth: true
    spacing: 0
    visible: section.cards.length > 0 || (section.surfaces.message || "") !== ""

    Component.onCompleted: {
        if (typeof section.bridge.refreshPluginCards === "function")
            section.bridge.refreshPluginCards()
    }

    SectionHeader {
        text: qsTr("From Plugins")
        level: 4
        Controls.ToolButton {
            objectName: "pluginCardsRefresh"
            icon.name: "view-refresh"
            text: qsTr("Refresh")
            display: Controls.AbstractButton.IconOnly
            enabled: !section.working
            Accessible.name: text
            Controls.ToolTip.text: text
            Controls.ToolTip.visible: hovered
            onClicked: section.bridge.refreshPluginCards()
        }
    }

    Repeater {
        model: section.cards
        delegate: ColumnLayout {
            id: plugin
            required property var modelData
            objectName: "pluginCard_" + plugin.modelData.pluginId
            Layout.fillWidth: true
            spacing: 0

            // Plugin name only when there are several, like a sub-heading.
            Controls.Label {
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                visible: section.cards.length > 1 && plugin.modelData.ok
                text: plugin.modelData.name
                textFormat: Text.PlainText
                elide: Text.ElideRight
                font: Kirigami.Theme.smallFont
                color: Kirigami.Theme.disabledTextColor
            }
            ListRow {
                objectName: "pluginCardHint"
                Layout.fillWidth: true
                visible: !plugin.modelData.ok
                density: "compact"
                wrapSubtitle: true
                dimmed: true
                avatarSize: Kirigami.Units.iconSizes.smallMedium
                iconName: "dialog-warning"
                title: plugin.modelData.name
                subtitle: plugin.modelData.hint
                focusPolicy: Qt.NoFocus
                hoverEnabled: false
            }
            Repeater {
                model: plugin.modelData.items
                delegate: ListRow {
                    id: item
                    required property var modelData
                    readonly property var primary: (item.modelData.actions || [])
                        .find(action => action.primary === true) || null
                    objectName: "pluginItem_" + item.modelData.id
                    Layout.fillWidth: true
                    density: "compact"
                    wrapSubtitle: true
                    avatarSize: Kirigami.Units.iconSizes.smallMedium
                    iconName: item.modelData.icon
                    title: item.modelData.title
                    subtitle: item.modelData.subtitle
                    pinActions: true
                    enabled: !section.working
                    // A click on the row runs the item's primary action.
                    onClicked: if (item.primary !== null)
                        section.bridge.invokePluginAction(plugin.modelData.pluginId,
                                                          item.modelData.id, item.primary.id)

                    Repeater {
                        model: (item.modelData.actions || []).filter(action => action.primary !== true)
                        delegate: Controls.ToolButton {
                            id: actionButton
                            required property var modelData
                            objectName: "pluginAction_" + actionButton.modelData.id
                            text: actionButton.modelData.label
                            icon.name: actionButton.modelData.icon
                            display: actionButton.modelData.icon !== ""
                                ? Controls.AbstractButton.IconOnly : Controls.AbstractButton.TextOnly
                            Accessible.name: text
                            Controls.ToolTip.text: text
                            Controls.ToolTip.visible: hovered && display === Controls.AbstractButton.IconOnly
                            onClicked: section.bridge.invokePluginAction(
                                plugin.modelData.pluginId, item.modelData.id, actionButton.modelData.id)
                        }
                    }
                    Controls.ToolButton {
                        objectName: "pluginPrimaryAction"
                        visible: item.primary !== null
                        text: item.primary !== null ? item.primary.label : ""
                        icon.name: item.primary !== null && item.primary.icon !== ""
                            ? item.primary.icon : "go-next"
                        display: Controls.AbstractButton.IconOnly
                        Accessible.name: text
                        Controls.ToolTip.text: text
                        Controls.ToolTip.visible: hovered
                        onClicked: section.bridge.invokePluginAction(
                            plugin.modelData.pluginId, item.modelData.id, item.primary.id)
                    }
                }
            }
        }
    }

    Notice {
        id: notice
        objectName: "pluginCardMessage"
        Layout.leftMargin: Kirigami.Units.largeSpacing
        Layout.rightMargin: Kirigami.Units.largeSpacing
        Layout.topMargin: Kirigami.Units.smallSpacing
        plainText: section.surfaces.message || ""
        type: section.surfaces.messageOk === false
            ? Kirigami.MessageType.Warning : Kirigami.MessageType.Positive
        showCloseButton: true
        onVisibleChanged: {
            if (visible)
                return
            if ((section.surfaces.message || "") !== "")
                section.bridge.clearPluginCardMessage()
            visible = Qt.binding(function() { return notice.plainText !== "" })
        }
    }
}
