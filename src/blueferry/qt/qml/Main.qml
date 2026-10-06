pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root

    ConversationLogic { id: conversationLogic }

    required property var bridge
    property string selectedThreadKey: ""
    onSelectedThreadKeyChanged: root.markSelectedThreadRead()
    onActiveChanged: root.markSelectedThreadRead()
    onIphoneSettingsPageChanged: Qt.callLater(root.markSelectedThreadRead)
    property bool firstRunRedirected: false
    property var iphoneSettingsPage: null
    property string pendingMessageHandle: ""

    visible: true
    width: Kirigami.Units.gridUnit * 62
    height: Kirigami.Units.gridUnit * 38
    minimumWidth: 420
    minimumHeight: 480
    title: qsTr("BlueFerry")

    function threadByKey(key) {
        return conversationLogic.threadByKey(bridge.threads, key)
    }

    function selectedThread() {
        return threadByKey(selectedThreadKey)
    }

    function threadIsUnread(thread) {
        return conversationLogic.threadIsUnread(thread)
    }

    function markSelectedThreadRead() {
        if (!root.visible || !root.active || root.iphoneSettingsPage !== null)
            return
        const thread = selectedThread()
        if (thread && threadIsUnread(thread))
            bridge.markThreadRead(thread.key)
    }

    function selectMessage(handle) {
        const thread = conversationLogic.threadForMessage(bridge.threads, handle)
        if (!thread) return false
        showTab(messagesTab)
        selectedThreadKey = thread.key
        pendingMessageHandle = ""
        return true
    }

    function htmlEscape(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
    }

    function previewLine(value) {
        // One calm line per conversation, however the message is formatted.
        return String(value || "").replace(/\s+/g, " ").trim()
    }

    function escapedRichText(value) {
        // SelectableLabel uses AutoText. The wrapper makes escaped entities
        // render as text instead of appearing literally as "&amp;".
        return "<span>" + value + "</span>"
    }

    function mapConnectionRefused() {
        const status = bridge.status || ({})
        return status.map_connection_refused === true
    }

    function leBondSuspect() {
        const status = bridge.status || ({})
        return status.le_bond_suspect === true
    }

    function retainedStorageUnavailable() {
        const status = bridge.status || ({})
        return status.daemon === true
            && status.storage_policy !== undefined
            && status.storage_policy !== "none"
            && status.storage_state !== "ready"
    }

    function storageDetail() {
        const status = bridge.status || ({})
        if (status.storage_detail)
            return status.storage_detail
        return qsTr("Local conversation history is unavailable.")
    }

    function openPhoneSettings() {
        // Utility pages belong in Kirigami's PageRow. Its modal layers are an
        // anchored StackView internally: animated pushes warn about those
        // anchors, while forcing an Immediate push can create an empty layer.
        if (iphoneSettingsPage !== null) {
            pageStack.currentIndex = pageStack.depth - 1
            return
        }
        iphoneSettingsPage = pageStack.push(iphonePageLoader.item)
    }

    function closePhoneSettings() {
        if (iphoneSettingsPage === null)
            return
        const page = iphoneSettingsPage
        iphoneSettingsPage = null
        pageStack.removePage(page)
    }

    // Tabs of the main page. Opt-in data is fetched only while its tab is
    // shown and forgotten when it is left.
    readonly property int messagesTab: 0
    readonly property int callsTab: 1
    readonly property int notificationsTab: 2
    property int currentTab: messagesTab
    property bool callHistoryWatched: false
    property bool notificationsWatched: false
    onCurrentTabChanged: root.syncWatches()

    function syncWatches() {
        const calls = root.currentTab === root.callsTab && bridge.callHistoryEnabled === true
        if (calls !== root.callHistoryWatched) {
            root.callHistoryWatched = calls
            bridge.watchCallHistory(calls)
        }
        const notifications = root.currentTab === root.notificationsTab
        if (notifications !== root.notificationsWatched) {
            root.notificationsWatched = notifications
            bridge.watchNotifications(notifications)
        }
    }

    function showTab(tab) {
        closePhoneSettings()
        pageStack.currentIndex = 0
        currentTab = tab
    }

    function openRecentCalls() {
        showTab(callsTab)
    }

    function togglePhoneSettings() {
        if (iphoneSettingsPage !== null)
            closePhoneSettings()
        else
            openPhoneSettings()
    }

    function warnAboutRosterChanges() {
        const thread = conversationLogic.nextRosterWarning(bridge.threads)
        if (!thread) return
        rosterChangedDialog.thread = thread
        rosterChangedDialog.open()
    }

    Connections {
        target: root.bridge

        function onThreadsChanged() {
            const selected = root.selectedThread()
            root.selectedThreadKey = selected ? selected.key : ""
            if (root.pendingMessageHandle !== "")
                root.selectMessage(root.pendingMessageHandle)
            root.warnAboutRosterChanges()
            root.markSelectedThreadRead()
        }

        function onMessageOpenRequested(handle) {
            root.pendingMessageHandle = handle
            if (!root.selectMessage(handle))
                root.bridge.refresh()
        }

        function onStatusChanged() {
            root.syncWatches()
        }

        function onSetupLoadedChanged() {
            if (root.bridge.setupLoaded && !root.bridge.configured && !root.firstRunRedirected) {
                root.firstRunRedirected = true
                root.openPhoneSettings()
            }
        }

    }

    Shortcut {
        sequences: [StandardKey.Refresh]
        onActivated: root.bridge.refresh()
    }
    Shortcut {
        sequence: "Ctrl+Q"
        onActivated: Qt.quit()
    }
    Shortcut {
        sequence: "Ctrl+?"
        onActivated: shortcutsDialog.open()
    }

    pageStack.initialPage: messagesPage

    Kirigami.PromptDialog {
        id: rosterChangedDialog
        property var thread: null
        title: qsTr("Group Membership May Have Changed")
        subtitle: thread
            ? root.escapedRichText(
                qsTr("%1 sent a message to %2, but is not in BlueFerry's saved participant list. Replies are disabled until you review the list. This can also happen if you have multiple groups named %2, because BlueFerry cannot distinguish them.")
                    .arg(root.htmlEscape(thread.unexpected_sender || qsTr("Someone new")))
                    .arg(root.htmlEscape(thread.name))
              )
            : ""
        dialogType: Kirigami.PromptDialog.Warning
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [Kirigami.Action {
            text: qsTr("Review Participants")
            icon.name: "system-users"
            onTriggered: {
                const selected = rosterChangedDialog.thread
                rosterChangedDialog.close()
                groupParticipantsDialog.thread = selected
                groupParticipantsDialog.open()
            }
        }]
    }

    Kirigami.Dialog {
        id: groupParticipantsDialog
        property var thread: null
        title: thread ? qsTr("Who is in %1?").arg(thread.name) : ""
        preferredWidth: Kirigami.Units.gridUnit * 28
        standardButtons: Kirigami.Dialog.Cancel

        function recipients() {
            return conversationLogic.participantLines(groupParticipantEditor.text)
        }

        customFooterActions: [Kirigami.Action {
            text: qsTr("Save Participants")
            icon.name: "document-save"
            enabled: groupParticipantsDialog.recipients().length >= 2
                && !root.bridge.busy
            onTriggered: {
                root.bridge.setGroupParticipants(
                    groupParticipantsDialog.thread.key,
                    groupParticipantsDialog.recipients()
                )
                groupParticipantsDialog.close()
            }
        }]

        onOpened: {
            groupParticipantEditor.text = thread
                ? (thread.recipients || []).join("\n") : ""
            groupParticipantEditor.forceActiveFocus()
        }

        ColumnLayout {
            spacing: Kirigami.Units.smallSpacing

            Controls.Label {
                Layout.fillWidth: true
                text: groupParticipantsDialog.thread
                    ? qsTr("%1 has sent a message to a group named %2, which you're a member of. BlueFerry can't determine the participants of this group chat, but if you fill in the members, it can work.")
                        .arg(groupParticipantsDialog.thread.prompt_sender || qsTr("Someone"))
                        .arg(groupParticipantsDialog.thread.name)
                    : ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            Controls.Label {
                Layout.fillWidth: true
                text: qsTr("Enter every other participant's phone number or Apple ID email, one per line.")
                wrapMode: Text.Wrap
            }
            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: true
                type: Kirigami.MessageType.Information
                text: qsTr("Changing this list only updates BlueFerry's local understanding of the group. It does not add or remove anyone in Messages on your iPhone.")
            }
            Controls.TextArea {
                id: groupParticipantEditor
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.gridUnit * 7
                placeholderText: qsTr("One participant per line")
                wrapMode: TextEdit.NoWrap
                Accessible.name: qsTr("Group Participants")
            }
            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: true
                type: Kirigami.MessageType.Warning
                text: groupParticipantsDialog.thread
                    ? root.escapedRichText(
                        qsTr("BlueFerry identifies named groups by name. If you have multiple groups named %1, BlueFerry may combine them and use the wrong participant list. This list can also become outdated if the group is renamed or its membership changes.")
                            .arg(root.htmlEscape(groupParticipantsDialog.thread.name))
                      )
                    : ""
            }
        }
    }

    Kirigami.PromptDialog {
        id: clearDialog
        title: qsTr("Clear Local History?")
        subtitle: qsTr("This deletes local message history and group metadata. Nothing is deleted from the iPhone.")
        dialogType: Kirigami.PromptDialog.Warning
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [Kirigami.Action {
            text: qsTr("Clear History")
            icon.name: "edit-clear-history"
            onTriggered: {
                root.bridge.clearHistory()
                clearDialog.close()
            }
        }]
    }

    Kirigami.PromptDialog {
        id: deleteThreadsDialog
        property string threadKey: ""
        title: qsTr("Delete Conversation?")
        subtitle: qsTr("This permanently deletes this local message history and group metadata. Nothing is deleted from your iPhone. A new message can create the conversation again.")
        dialogType: Kirigami.PromptDialog.Warning
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [Kirigami.Action {
            text: qsTr("Delete Locally")
            icon.name: "edit-delete"
            enabled: !root.bridge.busy
            onTriggered: {
                root.bridge.deleteThreads([deleteThreadsDialog.threadKey])
                deleteThreadsDialog.close()
            }
        }]
    }

    Controls.Menu {
        id: threadContextMenu
        property string threadKey: ""

        Controls.MenuItem {
            text: {
                const thread = root.threadByKey(threadContextMenu.threadKey)
                return thread && thread.starred
                    ? qsTr("Unstar Conversation") : qsTr("Star Conversation")
            }
            icon.name: {
                const thread = root.threadByKey(threadContextMenu.threadKey)
                return thread && thread.starred
                    ? "non-starred-symbolic" : "starred-symbolic"
            }
            onTriggered: {
                const thread = root.threadByKey(threadContextMenu.threadKey)
                root.bridge.setThreadStarred(
                    threadContextMenu.threadKey,
                    !(thread && thread.starred)
                )
            }
        }
        Controls.MenuItem {
            text: qsTr("Delete Conversation")
            icon.name: "edit-delete"
            onTriggered: {
                deleteThreadsDialog.threadKey = threadContextMenu.threadKey
                deleteThreadsDialog.open()
            }
        }
    }

    PhoneSettingsDialogs {
        id: phoneSettingsDialogs
        anchors.fill: parent
        bridge: root.bridge
    }

    Kirigami.PromptDialog {
        id: shortcutsDialog
        title: qsTr("Keyboard Shortcuts")
        subtitle: qsTr("Refresh — Ctrl+R\nQuit — Ctrl+Q\nKeyboard Shortcuts — Ctrl+?")
        standardButtons: Kirigami.Dialog.Close
    }

    GroupConfirmationDialog {
        id: confirmGroupDialog
        bridge: root.bridge
    }

    NewMessageDialog {
        id: newMessageDialog
        bridge: root.bridge
    }

    // Optional HFP calls: nothing is instantiated unless the backend enables
    // them, so the default window is unchanged.
    Loader {
        id: callsLoader
        active: (root.bridge.status || {}).calls_enabled === true
        sourceComponent: CallsDialog {
            objectName: "callsDialog"
            bridge: root.bridge
        }
    }

    Kirigami.Page {
        id: messagesPage
        objectName: "messagesPage"
        visible: false
        title: qsTr("BlueFerry")
        padding: 0
        // The phone card collapses behind a header button on small windows.
        property bool compact: width < Kirigami.Units.gridUnit * 56
        property bool cardOpen: false
        property bool narrow: messagesSplit.width < Kirigami.Units.gridUnit * 30
        property var thread: root.selectedThread()

        actions: [
            Kirigami.Action {
                text: qsTr("Phone")
                icon.name: "smartphone"
                visible: messagesPage.compact
                checkable: true
                checked: messagesPage.cardOpen
                displayHint: Kirigami.DisplayHint.IconOnly
                onToggled: messagesPage.cardOpen = checked
            },
            Kirigami.Action {
                text: qsTr("New Message")
                icon.name: "list-add"
                displayHint: Kirigami.DisplayHint.IconOnly
                onTriggered: newMessageDialog.open()
            },
            Kirigami.Action {
                text: qsTr("Settings")
                icon.name: "configure"
                displayHint: Kirigami.DisplayHint.IconOnly
                onTriggered: root.togglePhoneSettings()
            }
        ]

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: root.bridge.errorText !== ""
                    text: root.htmlEscape(root.bridge.errorText)
                    type: Kirigami.MessageType.Error
                    position: Kirigami.InlineMessage.Position.Header
                    actions: [
                        Kirigami.Action {
                            text: qsTr("Open iPhone Settings")
                            onTriggered: root.openPhoneSettings()
                        }
                    ]
                }

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: root.mapConnectionRefused()
                    text: qsTr("iPhone is refusing message connections; is it connected to another computer?")
                    type: Kirigami.MessageType.Warning
                    position: Kirigami.InlineMessage.Position.Header
                    actions: [
                        Kirigami.Action {
                            text: qsTr("Open iPhone Settings")
                            onTriggered: root.openPhoneSettings()
                        }
                    ]
                }

                Kirigami.InlineMessage {
                    objectName: "leBondSuspectMessage"
                    Layout.fillWidth: true
                    visible: root.leBondSuspect()
                    text: qsTr("iPhone notifications can't connect because the Bluetooth pairing looks outdated. On the iPhone, open Settings > Bluetooth and forget this computer; then forget the iPhone here and pair again.")
                    type: Kirigami.MessageType.Warning
                    position: Kirigami.InlineMessage.Position.Header
                    actions: [
                        Kirigami.Action {
                            text: qsTr("Open iPhone Settings")
                            onTriggered: root.openPhoneSettings()
                        }
                    ]
                }

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: root.retainedStorageUnavailable()
                    text: root.htmlEscape(root.storageDetail())
                    type: Kirigami.MessageType.Warning
                    position: Kirigami.InlineMessage.Position.Header
                    actions: [
                        Kirigami.Action {
                            visible: root.bridge.status.storage_policy === "encrypted"
                            text: qsTr("Unlock Local Data")
                            icon.name: "document-decrypt"
                            enabled: !root.bridge.busy
                            onTriggered: root.bridge.unlockStorage()
                        },
                        Kirigami.Action {
                            text: qsTr("Open Settings")
                            onTriggered: root.openPhoneSettings()
                        }
                    ]
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    PhoneCard {
                        bridge: root.bridge
                        Layout.fillHeight: true
                        Layout.fillWidth: messagesPage.compact
                        Layout.preferredWidth: messagesPage.compact
                            ? messagesPage.width : Kirigami.Units.gridUnit * 17
                        visible: !messagesPage.compact || messagesPage.cardOpen
                    }

                    Kirigami.Separator {
                        Layout.fillHeight: true
                        visible: !messagesPage.compact
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: !messagesPage.compact || !messagesPage.cardOpen
                        spacing: 0

                        Controls.TabBar {
                            id: mainTabs
                            objectName: "mainTabs"
                            Layout.fillWidth: true
                            currentIndex: root.currentTab
                            onCurrentIndexChanged: root.currentTab = currentIndex

                            Controls.TabButton {
                                objectName: "messagesTabButton"
                                text: qsTr("Messages")
                                icon.name: "dialog-messages"
                            }
                            Controls.TabButton {
                                objectName: "callsTabButton"
                                text: qsTr("Calls")
                                icon.name: "call-start"
                            }
                            Controls.TabButton {
                                objectName: "notificationsTabButton"
                                text: qsTr("Notifications")
                                icon.name: "notifications"
                            }
                        }

                        StackLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            currentIndex: root.currentTab

                            Controls.SplitView {
                                id: messagesSplit
                                objectName: "messagesSplit"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                orientation: Qt.Horizontal

                                handle: Item {
                                    implicitWidth: messagesPage.narrow
                                        ? 0 : Kirigami.Units.smallSpacing
                                    visible: !messagesPage.narrow

                                    Kirigami.Separator {
                                        anchors.centerIn: parent
                                        height: parent.height
                                    }
                                }

                                ColumnLayout {
                                    objectName: "threadPane"
                                    Controls.SplitView.fillWidth: messagesPage.narrow
                                    Controls.SplitView.preferredWidth: messagesPage.narrow
                                        ? messagesSplit.width : messagesSplit.width * 0.35
                                    Controls.SplitView.minimumWidth: messagesPage.narrow
                                        ? 0 : Kirigami.Units.gridUnit * 12
                                    visible: !messagesPage.narrow || root.selectedThreadKey === ""
                                    spacing: 0

                                    Controls.ToolBar {
                                        Layout.fillWidth: true
                                        // Both pane headers share one height so their lines meet.
                                        Layout.preferredHeight: chatHeader.implicitHeight

                                        contentItem: Kirigami.Heading {
                                            level: 4
                                            text: qsTr("Conversations")
                                            leftPadding: Kirigami.Units.smallSpacing
                                            verticalAlignment: Text.AlignVCenter
                                            elide: Text.ElideRight
                                        }
                                    }

                                    ListView {
                                        id: threadList
                                        objectName: "threadList"
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        model: root.bridge.threads
                                        currentIndex: -1
                                        topMargin: Kirigami.Units.smallSpacing
                                        bottomMargin: Kirigami.Units.smallSpacing
                                        leftMargin: Kirigami.Units.smallSpacing
                                        rightMargin: Kirigami.Units.smallSpacing
                                        spacing: Kirigami.Units.smallSpacing / 2
                                        Controls.ScrollBar.vertical: Controls.ScrollBar {}

                                        delegate: Controls.ItemDelegate {
                                            id: threadDelegate
                                            objectName: "threadDelegate"
                                            required property var modelData
                                            readonly property var lastMessage: modelData.messages.length
                                                ? modelData.messages[modelData.messages.length - 1] : null
                                            readonly property bool unread: root.threadIsUnread(modelData)
                                            width: threadList.width - threadList.leftMargin - threadList.rightMargin
                                            highlighted: root.selectedThreadKey === modelData.key
                                            leftPadding: Kirigami.Units.largeSpacing
                                            rightPadding: Kirigami.Units.smallSpacing
                                            topPadding: Kirigami.Units.largeSpacing
                                            bottomPadding: Kirigami.Units.largeSpacing
                                            Accessible.name: threadDelegate.modelData.name
                                            onClicked: root.selectedThreadKey = modelData.key

                                            // A tint of the highlight colour instead of the full
                                            // selection block keeps long lists calm.
                                            background: Rectangle {
                                                radius: Kirigami.Units.cornerRadius
                                                color: threadDelegate.highlighted
                                                    ? Qt.alpha(Kirigami.Theme.highlightColor, 0.25)
                                                    : threadDelegate.hovered
                                                        ? Qt.alpha(Kirigami.Theme.highlightColor, 0.1)
                                                        : "transparent"
                                                border.width: threadDelegate.visualFocus ? 1 : 0
                                                border.color: Kirigami.Theme.focusColor
                                            }

                                            contentItem: RowLayout {
                                                spacing: Kirigami.Units.largeSpacing

                                                ContactAvatar {
                                                    Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                                                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                                                    bridge: root.bridge
                                                    group: threadDelegate.modelData.is_group
                                                    address: !threadDelegate.modelData.is_group
                                                        && threadDelegate.modelData.recipients.length === 1
                                                        ? threadDelegate.modelData.recipients[0] : ""
                                                }
                                                GridLayout {
                                                    Layout.fillWidth: true
                                                    columns: 2
                                                    rowSpacing: 0
                                                    columnSpacing: Kirigami.Units.smallSpacing

                                                    Controls.Label {
                                                        objectName: "threadName"
                                                        Layout.fillWidth: true
                                                        text: threadDelegate.modelData.name
                                                        textFormat: Text.PlainText
                                                        font.bold: true
                                                        color: Kirigami.Theme.textColor
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                    }
                                                    Controls.Label {
                                                        objectName: "threadTime"
                                                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                                        text: threadDelegate.lastMessage
                                                            ? threadDelegate.lastMessage.display_timestamp || "" : ""
                                                        textFormat: Text.PlainText
                                                        font: Kirigami.Theme.smallFont
                                                        color: threadDelegate.unread
                                                            ? Kirigami.Theme.highlightColor
                                                            : Kirigami.Theme.disabledTextColor
                                                    }
                                                    Controls.Label {
                                                        id: preview
                                                        objectName: "threadPreview"
                                                        Layout.fillWidth: true
                                                        text: threadDelegate.lastMessage
                                                            ? root.previewLine(threadDelegate.lastMessage.body)
                                                            : qsTr("No Messages")
                                                        textFormat: Text.PlainText
                                                        color: threadDelegate.unread
                                                            ? Kirigami.Theme.textColor
                                                            : Kirigami.Theme.disabledTextColor
                                                        elide: Text.ElideRight
                                                        wrapMode: Text.NoWrap
                                                        maximumLineCount: 1
                                                    }
                                                    Rectangle {
                                                        objectName: "threadUnreadDot"
                                                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                                        implicitWidth: Kirigami.Units.smallSpacing * 2
                                                        implicitHeight: implicitWidth
                                                        radius: width / 2
                                                        color: Kirigami.Theme.highlightColor
                                                        visible: threadDelegate.unread
                                                        Accessible.name: qsTr("Unread")
                                                    }
                                                }
                                                Controls.ToolButton {
                                                    // Always present for keyboard and screen-reader
                                                    // users; drawn only when starred or pointed at.
                                                    opacity: threadDelegate.modelData.starred
                                                        || threadDelegate.hovered || hovered || visualFocus ? 1 : 0
                                                    icon.name: threadDelegate.modelData.starred
                                                        ? "starred-symbolic" : "non-starred-symbolic"
                                                    text: threadDelegate.modelData.starred
                                                        ? qsTr("Unstar Conversation")
                                                        : qsTr("Star Conversation")
                                                    display: Controls.AbstractButton.IconOnly
                                                    Accessible.name: text
                                                    Controls.ToolTip.text: text
                                                    Controls.ToolTip.visible: hovered
                                                    onClicked: root.bridge.setThreadStarred(
                                                        threadDelegate.modelData.key,
                                                        !threadDelegate.modelData.starred
                                                    )
                                                }
                                            }
                                            TapHandler {
                                                acceptedButtons: Qt.RightButton
                                                onTapped: eventPoint => {
                                                    threadContextMenu.threadKey = threadDelegate.modelData.key
                                                    threadContextMenu.popup(
                                                        threadDelegate,
                                                        eventPoint.position.x,
                                                        eventPoint.position.y
                                                    )
                                                }
                                            }
                                        }

                                        Kirigami.PlaceholderMessage {
                                            anchors.centerIn: parent
                                            width: parent.width - Kirigami.Units.largeSpacing * 2
                                            visible: threadList.count === 0
                                            icon.name: "mail-message-new"
                                            text: root.bridge.status.storage_policy === "none"
                                                ? qsTr("Conversation History Disabled")
                                                : root.retainedStorageUnavailable()
                                                    ? qsTr("Conversation History Unavailable")
                                                    : qsTr("No Conversations Yet")
                                            explanation: root.bridge.status.storage_policy === "none"
                                                ? qsTr("Local messages are not being retained. Choose a storage option in Settings to keep conversation history.")
                                                : root.retainedStorageUnavailable()
                                                    ? root.storageDetail()
                                                    : qsTr("New iPhone messages will appear here.")
                                        }
                                    }
                                }

                                ColumnLayout {
                                    objectName: "chatPane"
                                    Controls.SplitView.fillWidth: true
                                    Controls.SplitView.minimumWidth: messagesPage.narrow
                                        ? 0 : Kirigami.Units.gridUnit * 18
                                    visible: !messagesPage.narrow || root.selectedThreadKey !== ""
                                    spacing: 0

                                    Controls.ToolBar {
                                        id: chatHeader
                                        objectName: "chatHeader"
                                        Layout.fillWidth: true

                                        contentItem: RowLayout {
                                            spacing: Kirigami.Units.largeSpacing

                                            Controls.ToolButton {
                                                visible: messagesPage.narrow
                                                icon.name: "go-previous"
                                                text: qsTr("Back")
                                                display: Controls.AbstractButton.IconOnly
                                                Accessible.name: text
                                                Controls.ToolTip.text: text
                                                Controls.ToolTip.visible: hovered
                                                onClicked: root.selectedThreadKey = ""
                                            }
                                            ContactAvatar {
                                                Layout.leftMargin: messagesPage.narrow ? 0 : Kirigami.Units.smallSpacing
                                                Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                                                Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                                                visible: messagesPage.thread !== null
                                                bridge: root.bridge
                                                group: messagesPage.thread !== null && messagesPage.thread.is_group
                                                address: messagesPage.thread !== null && !messagesPage.thread.is_group
                                                    && messagesPage.thread.recipients.length === 1
                                                    ? messagesPage.thread.recipients[0] : ""
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                Layout.leftMargin: messagesPage.thread === null
                                                    ? Kirigami.Units.smallSpacing : 0
                                                spacing: 0
                                                Kirigami.Heading {
                                                    objectName: "chatTitle"
                                                    Layout.fillWidth: true
                                                    level: 4
                                                    text: messagesPage.thread ? messagesPage.thread.name : qsTr("Conversation")
                                                    textFormat: Text.PlainText
                                                    elide: Text.ElideRight
                                                }
                                                Controls.Label {
                                                    objectName: "chatSubtitle"
                                                    Layout.fillWidth: true
                                                    visible: messagesPage.thread !== null && !messagesPage.thread.is_group
                                                    text: visible ? qsTr("Reply to: %1").arg(messagesPage.thread.recipients.join(", ")) : ""
                                                    textFormat: Text.PlainText
                                                    elide: Text.ElideRight
                                                    font: Kirigami.Theme.smallFont
                                                    color: Kirigami.Theme.disabledTextColor
                                                }
                                            }
                                            Controls.ToolButton {
                                                visible: messagesPage.thread !== null
                                                    && messagesPage.thread.group_origin === "named"
                                                icon.name: "system-users"
                                                text: qsTr("Edit Group Participants")
                                                display: Controls.AbstractButton.IconOnly
                                                Accessible.name: text
                                                Controls.ToolTip.text: text
                                                Controls.ToolTip.visible: hovered
                                                onClicked: {
                                                    groupParticipantsDialog.thread = messagesPage.thread
                                                    groupParticipantsDialog.open()
                                                }
                                            }
                                        }
                                    }

                                    Controls.Label {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: Kirigami.Units.largeSpacing
                                        Layout.rightMargin: Kirigami.Units.largeSpacing
                                        Layout.topMargin: Kirigami.Units.smallSpacing
                                        visible: messagesPage.thread !== null
                                            && messagesPage.thread.messages_truncated === true
                                        text: qsTr("Showing recent messages. Older messages remain in local history.")
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.Wrap
                                        font: Kirigami.Theme.smallFont
                                        color: Kirigami.Theme.disabledTextColor
                                    }

                                    ListView {
                                        id: messageList
                                        objectName: "messageList"
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        spacing: Kirigami.Units.largeSpacing
                                        bottomMargin: Kirigami.Units.largeSpacing
                                        // Short conversations sit just above the composer, like
                                        // a phone's message view.
                                        topMargin: Math.max(
                                            Kirigami.Units.largeSpacing,
                                            height - contentHeight - bottomMargin
                                        )
                                        model: messagesPage.thread ? messagesPage.thread.messages : []
                                        Controls.ScrollBar.vertical: Controls.ScrollBar {}

                                        delegate: Item {
                                            id: messageDelegate
                                            required property var modelData
                                            width: messageList.width
                                            implicitHeight: bubble.implicitHeight

                                            MessageBubble {
                                                id: bubble
                                                message: messageDelegate.modelData
                                                availableWidth: messageList.width - Kirigami.Units.largeSpacing * 2
                                                showSender: messagesPage.thread !== null
                                                    && messagesPage.thread.is_group
                                                anchors.right: messageDelegate.modelData.outgoing ? parent.right : undefined
                                                anchors.left: messageDelegate.modelData.outgoing ? undefined : parent.left
                                                anchors.leftMargin: Kirigami.Units.largeSpacing
                                                anchors.rightMargin: Kirigami.Units.largeSpacing * 2
                                            }
                                        }

                                        Kirigami.PlaceholderMessage {
                                            anchors.centerIn: parent
                                            width: parent.width - Kirigami.Units.largeSpacing * 4
                                            visible: messagesPage.thread === null || messageList.count === 0
                                            icon.name: "dialog-messages"
                                            text: messagesPage.thread === null
                                                ? qsTr("Select a Conversation") : qsTr("No Messages")
                                        }

                                        onCountChanged: Qt.callLater(positionViewAtEnd)
                                        onModelChanged: Qt.callLater(positionViewAtEnd)
                                    }

                                    Kirigami.InlineMessage {
                                        Layout.fillWidth: true
                                        Layout.margins: Kirigami.Units.smallSpacing
                                        visible: messagesPage.thread !== null
                                            && messagesPage.thread.participants_required === true
                                        type: Kirigami.MessageType.Information
                                        text: messagesPage.thread
                                            ? messagesPage.thread.roster_changed
                                                ? root.escapedRichText(
                                                    qsTr("%1 is not in BlueFerry's saved participant list for %2. Review the list before replying.")
                                                        .arg(root.htmlEscape(messagesPage.thread.unexpected_sender || qsTr("Someone new")))
                                                        .arg(root.htmlEscape(messagesPage.thread.name))
                                                  )
                                                : root.escapedRichText(
                                                    qsTr("%1 has sent a message to the group %2. BlueFerry needs its participant list before you can reply.")
                                                        .arg(root.htmlEscape(messagesPage.thread.prompt_sender || qsTr("Someone")))
                                                        .arg(root.htmlEscape(messagesPage.thread.name))
                                                  )
                                            : ""
                                        actions: [Kirigami.Action {
                                            text: qsTr("Add Participants")
                                            icon.name: "list-add-user"
                                            onTriggered: {
                                                groupParticipantsDialog.thread = messagesPage.thread
                                                groupParticipantsDialog.open()
                                            }
                                        }]
                                    }

                                    Kirigami.Separator { Layout.fillWidth: true }

                                    RowLayout {
                                        objectName: "composerRow"
                                        Layout.fillWidth: true
                                        Layout.margins: Kirigami.Units.largeSpacing
                                        spacing: Kirigami.Units.smallSpacing

                                        ExpandingMessageComposer {
                                            id: composer
                                            objectName: "messageComposer"
                                            Connections {
                                                target: root.bridge
                                                function onThreadSendSucceeded(key: string, body: string): void {
                                                    if (messagesPage.thread && messagesPage.thread.key === key
                                                            && composer.text === body)
                                                        composer.clear()
                                                }
                                            }
                                            placeholderText: qsTr("Write a Message")
                                            enabled: messagesPage.thread !== null
                                                && messagesPage.thread.reply_ready && !root.bridge.busy
                                            Accessible.name: qsTr("Message Text")
                                            onAccepted: sendButton.clicked()
                                        }
                                        Controls.Button {
                                            id: sendButton
                                            objectName: "sendButton"
                                            Layout.alignment: Qt.AlignBottom
                                            text: qsTr("Send")
                                            icon.name: "document-send"
                                            enabled: composer.enabled && composer.text.trim() !== ""
                                            Accessible.name: qsTr("Send Message")
                                            onClicked: {
                                                root.bridge.sendThread(
                                                    messagesPage.thread.key,
                                                    composer.text,
                                                    false
                                                )
                                            }
                                        }
                                    }
                                }
                            }

                            CallsTab {
                                bridge: root.bridge
                                onActiveCallsRequested: (callsLoader.item as CallsDialog)?.open()
                            }

                            NotificationsTab {
                                bridge: root.bridge
                            }
                        }
                    }
                }
        }
    }

    Component {
        id: aboutPage

        Kirigami.AboutPage {
            aboutData: ({
                displayName: qsTr("BlueFerry"),
                productName: "BlueFerry",
                componentName: "BlueFerry",
                shortDescription: qsTr("Messages, contacts, and notifications from a paired iPhone"),
                homepage: "https://github.com/erikwb/blueferry",
                bugAddress: "https://github.com/erikwb/blueferry/issues",
                version: root.bridge.version,
                otherText: "",
                authors: [],
                credits: [],
                translators: [],
                licenses: [{name: "GPL-2.0-or-later", text: "", spdx: "GPL-2.0-or-later"}],
                copyrightStatement: qsTr("Copyright © 2026 Erik Bourget <erik@ebourget.net>\nCopyright © 2026 Gabe Shatunovsky <gabriel@shatunovsky.com>"),
                desktopFileName: "io.weirdware.BlueFerry.Qt"
            })
        }
    }

    Loader {
        id: iphonePageLoader
        // PageRow creates Component-backed pages under an internal QtObject.
        // Keep this page visually parented and alive so Qt does not warn while
        // Kirigami is still incubating its toolbar delegates.
        asynchronous: false
        visible: false
        sourceComponent: iphonePageComponent
    }

    Component {
        id: iphonePageComponent

        PhoneSettingsPage {
            bridge: root.bridge
            onCloseRequested: root.closePhoneSettings()
            onClearHistoryRequested: clearDialog.open()
            onShortcutsRequested: shortcutsDialog.open()
            onAboutRequested: {
                root.closePhoneSettings()
                root.pageStack.layers.push(aboutPage)
            }
            onBluetoothRestartRequested: phoneSettingsDialogs.requestBluetoothRestart()
            onPairingIssueRequested: phoneSettingsDialogs.showPairingIssue()
            onForgetRequested: mac => phoneSettingsDialogs.requestForget(mac)
            onStoragePolicyRequested: policy => phoneSettingsDialogs.requestStoragePolicy(policy)
            onPairingRequested: (mac, paired, compatibilityMode, explicitPairing) =>
                phoneSettingsDialogs.requestPairing(mac, paired, compatibilityMode, explicitPairing)
        }
    }
}
