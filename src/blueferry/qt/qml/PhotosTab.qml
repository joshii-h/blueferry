pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Newest photos from a photos plugin (PLUGINS.md), loaded only while this
// tab is shown. Without a plugin, or before its setup, the tab shows a dimmed
// hint with the setup command instead. Labels are plain text; images are
// local cache files the bridge has already checked.
ColumnLayout {
    id: tab
    objectName: "photosTab"
    required property var bridge
    readonly property var photos: tab.bridge.photos || ({})
    readonly property var items: tab.photos.items || []
    spacing: 0

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.smallSpacing
        visible: tab.photos.ready === true
        Controls.Label {
            objectName: "photosSummary"
            Layout.fillWidth: true
            text: tab.photos.hint || ""
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Kirigami.Theme.disabledTextColor
        }
        Controls.ToolButton {
            objectName: "photosRefresh"
            icon.name: "view-refresh"
            text: qsTr("Refresh")
            display: Controls.AbstractButton.IconOnly
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: text
            onClicked: tab.bridge.refreshPhotos()
        }
    }

    Kirigami.PlaceholderMessage {
        objectName: "photosPlaceholder"
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.margins: Kirigami.Units.gridUnit
        visible: tab.photos.loaded === true && tab.photos.ready !== true
        enabled: false
        icon.name: "folder-pictures"
        text: tab.photos.present === true ? qsTr("Photos are not available") : qsTr("No photo plugin")
        explanation: tab.photos.hint || ""
    }

    Controls.BusyIndicator {
        Layout.alignment: Qt.AlignCenter
        Layout.fillHeight: true
        visible: tab.photos.loaded !== true
        running: visible
    }

    Controls.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: tab.photos.ready === true

        GridView {
            id: grid
            objectName: "photosGrid"
            clip: true
            model: tab.items
            cellWidth: Kirigami.Units.gridUnit * 9
            cellHeight: Kirigami.Units.gridUnit * 10

            delegate: Item {
                id: cell
                required property var modelData
                width: grid.cellWidth
                height: grid.cellHeight

                Rectangle {
                    id: frame
                    anchors.fill: parent
                    anchors.margins: Kirigami.Units.smallSpacing
                    radius: Kirigami.Units.cornerRadius
                    color: hover.hovered ? Kirigami.Theme.hoverColor : "transparent"

                    Image {
                        id: thumbnail
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: Kirigami.Units.smallSpacing
                        height: parent.height - caption.height - Kirigami.Units.largeSpacing
                        source: cell.modelData.thumbnail
                        sourceSize.width: 256
                        sourceSize.height: 256
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: false
                    }
                    Kirigami.Icon {
                        anchors.centerIn: thumbnail
                        width: Kirigami.Units.iconSizes.medium
                        height: width
                        visible: thumbnail.status !== Image.Ready || cell.modelData.video === true
                        source: cell.modelData.video === true ? "media-playback-start" : "image-x-generic"
                    }
                    Controls.Label {
                        id: caption
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: Kirigami.Units.smallSpacing
                        text: cell.modelData.label
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        horizontalAlignment: Text.AlignHCenter
                        font: Kirigami.Theme.smallFont
                    }

                    HoverHandler { id: hover }
                    TapHandler {
                        onTapped: tab.bridge.openPhoto(cell.modelData.id)
                    }
                    // Drag the original into other apps once it is downloaded
                    // (opening a photo downloads it).
                    DragHandler {
                        id: drag
                        enabled: cell.modelData.original !== ""
                        target: null
                    }
                    Drag.active: drag.active
                    Drag.dragType: Drag.Automatic
                    Drag.supportedActions: Qt.CopyAction
                    Drag.mimeData: ({"text/uri-list": cell.modelData.original})

                    Controls.ToolTip.visible: hover.hovered
                    Controls.ToolTip.delay: Kirigami.Units.toolTipDelay
                    Controls.ToolTip.text: cell.modelData.original !== ""
                        ? qsTr("Click to open, or drag the file into another app")
                        : qsTr("Click to download and open")
                }
            }
        }
    }
}
