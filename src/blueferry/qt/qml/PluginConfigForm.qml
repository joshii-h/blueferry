pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// A settings form generated from a plugin's [Config …] schema. A stored
// secret is never shown: its field stays empty ("leave empty to keep") and
// only a newly typed value is sent. The plugin validates and answers with
// per-field reasons, shown under the fields.
ColumnLayout {
    id: form
    objectName: "pluginConfigForm"
    required property var bridge
    required property var config
    property bool busy: false
    property var draft: ({})
    spacing: Kirigami.Units.smallSpacing

    function set(key, value) {
        const next = Object.assign({}, form.draft)
        next[key] = value
        form.draft = next
    }

    Controls.Label {
        visible: form.config.loaded !== true
        text: qsTr("Loading the plugin's settings…")
    }
    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: ((form.config.errors || ({}))[""] || "") !== ""
        type: Kirigami.MessageType.Error
        text: (form.config.errors || ({}))[""] || ""
    }
    Kirigami.FormLayout {
        Layout.fillWidth: true
        enabled: form.config.loaded === true && !form.busy

        Repeater {
            model: form.config.fields || []
            delegate: ColumnLayout {
                id: fieldRow
                required property var modelData
                readonly property string error: (form.config.errors || ({}))[fieldRow.modelData.key] || ""
                Kirigami.FormData.label: fieldRow.modelData.label + (fieldRow.modelData.required ? " *" : "") + ":"
                Layout.fillWidth: true
                spacing: 0

                Controls.TextField {
                    objectName: "configField_" + fieldRow.modelData.key
                    visible: ["string", "url", "secret"].indexOf(fieldRow.modelData.type) >= 0
                    Layout.fillWidth: true
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 14
                    echoMode: fieldRow.modelData.type === "secret" ? TextInput.Password : TextInput.Normal
                    text: fieldRow.modelData.type === "secret" ? "" : String(fieldRow.modelData.value || "")
                    placeholderText: fieldRow.modelData.type === "secret" && fieldRow.modelData.stored
                        ? qsTr("Stored — leave empty to keep") : ""
                    onTextEdited: form.set(fieldRow.modelData.key, text)
                }
                Controls.Switch {
                    visible: fieldRow.modelData.type === "bool"
                    checked: fieldRow.modelData.value === true
                    onToggled: form.set(fieldRow.modelData.key, checked)
                }
                Controls.SpinBox {
                    visible: fieldRow.modelData.type === "int"
                    from: fieldRow.modelData.minimum
                    to: fieldRow.modelData.maximum
                    editable: true
                    value: fieldRow.modelData.type === "int" ? Number(fieldRow.modelData.value) : 0
                    onValueModified: form.set(fieldRow.modelData.key, value)
                }
                Controls.ComboBox {
                    visible: fieldRow.modelData.type === "choice"
                    model: fieldRow.modelData.choices || []
                    currentIndex: Math.max(0, (fieldRow.modelData.choices || []).indexOf(fieldRow.modelData.value))
                    onActivated: form.set(fieldRow.modelData.key, currentText)
                }
                Controls.Label {
                    Layout.fillWidth: true
                    visible: (fieldRow.modelData.help || "") !== ""
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.disabledTextColor
                    text: fieldRow.modelData.help || ""
                }
                Controls.Label {
                    Layout.fillWidth: true
                    visible: fieldRow.error !== ""
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    color: Kirigami.Theme.negativeTextColor
                    text: fieldRow.error
                }
            }
        }
    }
    RowLayout {
        Controls.Button {
            objectName: "pluginConfigSave"
            text: qsTr("Save")
            icon.name: "document-save"
            enabled: form.config.loaded === true && !form.busy && Object.keys(form.draft).length > 0
            onClicked: form.bridge.savePluginConfig(form.config.id, form.draft)
        }
    }
}
