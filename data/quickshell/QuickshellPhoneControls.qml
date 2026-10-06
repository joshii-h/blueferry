pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts

// iPhone now playing with previous/play-pause/next and the sound, hotspot and
// lock-when-away switches. The bridge's phone_overview call returns the state
// already reduced by blueferry.phone_overview, so this file only renders it.
// Remote text (track titles) is shown with Text.PlainText via FerryLabel.
ColumnLayout {
  id: root

  required property var ferryTheme
  required property var bridge
  property var overview: ({})
  property string errorText: ""

  readonly property var media: root.overview.media || ({})
  readonly property var commands: root.media.commands || []
  readonly property var phoneMethods: [
    "phone_overview", "media_command", "set_phone_audio_route", "set_tether",
    "set_proximity_lock"
  ]

  spacing: root.ferryTheme.scaled(4)

  function refresh() {
    root.bridge.requestLatest("phone_overview", {})
  }

  function act(method, args) {
    root.errorText = ""
    root.bridge.request(method, args)
  }

  function part(name) {
    return root.overview[name] || ({available: false, hint: ""})
  }

  Component.onCompleted: root.refresh()

  Connections {
    target: root.bridge

    function onResponse(method, requestId, result) {
      if (method === "phone_overview") {
        root.overview = typeof result === "object" && result !== null ? result : ({})
      } else if (root.phoneMethods.indexOf(method) >= 0) {
        root.refresh()
      }
    }

    function onFailure(method, requestId, message) {
      if (root.phoneMethods.indexOf(method) < 0) return
      root.errorText = message || "iPhone request failed"
      if (method !== "phone_overview") root.refresh()
    }

    function onEventReceived(name, data) {
      if (name === "phone-changed" || name === "status-changed") root.refresh()
    }
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: root.ferryTheme.scaled(6)

    FerryLabel {
      objectName: "phoneMediaTitle"
      ferryTheme: root.ferryTheme
      Layout.fillWidth: true
      elide: Text.ElideRight
      text: root.media.available ? root.media.title || "" : root.media.hint || ""
      color: root.media.available ? root.ferryTheme.windowText : root.ferryTheme.muted
      font.pixelSize: root.ferryTheme.captionSize
      wrapMode: root.media.available ? Text.NoWrap : Text.Wrap
    }
    Repeater {
      model: [
        {command: "previous", label: "⏮"},
        {command: "toggle", label: root.media.playing ? "⏸" : "▶"},
        {command: "next", label: "⏭"}
      ]
      delegate: FerryButton {
        required property var modelData
        ferryTheme: root.ferryTheme
        visible: root.media.available === true
        enabled: root.commands.indexOf(modelData.command) >= 0
        text: modelData.label
        Accessible.name: modelData.command
        onClicked: root.act("media_command", {command: modelData.command})
      }
    }
  }

  FerryCheckBox {
    objectName: "phoneAudioSwitch"
    ferryTheme: root.ferryTheme
    Layout.fillWidth: true
    readonly property var info: root.part("audio")
    enabled: info.available === true
    checked: info.on_pc === true
    text: "iPhone sound on this computer — " + (info.hint || "")
    onToggled: {
      root.act("set_phone_audio_route", {route: checked ? "pc" : "phone"})
      checked = Qt.binding(() => info.on_pc === true)
    }
  }
  FerryCheckBox {
    ferryTheme: root.ferryTheme
    Layout.fillWidth: true
    objectName: "phoneTetherSwitch"
    readonly property var info: root.part("tether")
    enabled: info.available === true
    checked: info.active === true
    text: "Personal Hotspot — " + (info.hint || "")
    onToggled: {
      root.act("set_tether", {enabled: checked})
      checked = Qt.binding(() => info.active === true)
    }
  }
  FerryCheckBox {
    ferryTheme: root.ferryTheme
    Layout.fillWidth: true
    readonly property var info: root.part("proximity")
    enabled: info.available === true
    checked: info.enabled === true
    text: "Lock when the iPhone goes away — " + (info.hint || "")
    onToggled: {
      root.act("set_proximity_lock", {enabled: checked, grace_seconds: info.grace || 0})
      checked = Qt.binding(() => info.enabled === true)
    }
  }

  FerryLabel {
    ferryTheme: root.ferryTheme
    Layout.fillWidth: true
    visible: root.errorText !== ""
    text: root.errorText
    wrapMode: Text.Wrap
    color: root.ferryTheme.muted
    font.pixelSize: root.ferryTheme.captionSize
  }
}
