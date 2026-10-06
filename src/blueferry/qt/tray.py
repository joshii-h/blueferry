"""``blueferry-tray``: a small system tray item for the running BlueFerry daemon.

Under Plasma ``QSystemTrayIcon`` becomes a StatusNotifierItem. The icon is a
phone with the number of unread messages painted over it, the tooltip shows
the connection plus battery, signal and network, and the menu opens the Qt
client, moves the iPhone's sound, switches the Personal Hotspot and starts
the companion tools (screen mirroring, LocalSend, iPhone photos over USB).

The tray is a thin client of the existing session-bus API. Every call is
asynchronous and sent with auto-start disabled: it follows a running daemon
and never starts (or restarts) one. GetStatus, ListThreads and
Tether1.GetState are fetched again after the content-free StatusChanged,
HistoryChanged and TetherChanged signals, coalesced by a short timer.
"""
from __future__ import annotations

import shutil
import signal
import sys
from collections.abc import Callable

from PySide6.QtCore import (
    SLOT,
    QObject,
    QProcess,
    QProcessEnvironment,
    QRect,
    Qt,
    QThreadPool,
    QTimer,
    Slot,
)
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
    QDBusServiceWatcher,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QStyle, QSystemTrayIcon

from blueferry import plugin_surfaces as surfaces
from blueferry.client_activation import (
    ACTIVATION_INTERFACE,
    ACTIVATION_PATH,
    CLIENTS,
    TRAY_BUS_NAME,
)
from blueferry.client_wire import decode_mapping, decode_threads
from blueferry.i18n import _
from blueferry.models import BackendStatus
from blueferry.protocol import (
    BUS_NAME,
    EVENTS_IFACE,
    MEDIA_IFACE,
    MESSAGES_IFACE,
    OBJECT_PATH,
    TETHER_IFACE,
    backend_compatibility_error,
)
from blueferry.qt import tray_presenter as presenter
from blueferry.qt.companion import CompanionTools
from blueferry.qt.tasks import Task
from blueferry.reconnect_view import (
    RATE_LIMITED_TEXT,
    is_rate_limited,
    reconnect_error_text,
    result_text,
)
from blueferry.tether_status import TetherStatus

QT_CLIENT = next(client for client in CLIENTS if client.key == "qt")
APP_ICON = "io.weirdware.BlueFerry"
PHONE_ICONS = ("smartphone", "phone", APP_ICON)
ICON_SIZES = (16, 22, 24, 32, 48, 64)
THREAD_LIMIT = 500
CALL_TIMEOUT_MS = 10_000
ACTION_TIMEOUT_MS = 30_000
REFRESH_DELAY_MS = 150

Reply = Callable[[list], None]
Failure = Callable[[str], None]


def phone_icon() -> QIcon:
    for name in PHONE_ICONS:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    # No icon theme at all: still show something the user can click.
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)


def badged_icon(base: QIcon, badge: str, *, dimmed: bool = False) -> QIcon:
    """The phone icon with ``badge`` in a red circle at its top right."""
    icon = QIcon()
    mode = QIcon.Mode.Disabled if dimmed else QIcon.Mode.Normal
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base.paint(painter, QRect(0, 0, size, size), mode=mode)
        if badge:
            diameter = max(8, round(size * 0.5))
            width = min(size, max(diameter, round(diameter * (0.45 + 0.3 * len(badge)))))
            rect = QRect(size - width, 0, width, diameter)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#da4453"))  # Breeze "negative"
            painter.drawRoundedRect(rect, diameter / 2, diameter / 2)
            font = QFont()
            font.setBold(True)
            pixels = max(6, round(diameter * 0.75))
            font.setPixelSize(pixels)
            # Shrink long counts ("99+") until they fit inside the pill.
            while pixels > 6 and QFontMetrics(font).horizontalAdvance(badge) > width - 2:
                pixels -= 1
                font.setPixelSize(pixels)
            painter.setFont(font)
            painter.setPen(QColor("white"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, badge)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def theme_icon(key: str) -> QIcon:
    return QIcon.fromTheme(presenter.ICONS.get(key, ""))


class TrayController(QObject):
    """Owns the tray icon and mirrors the daemon's state into it."""

    def __init__(
        self,
        bus: QDBusConnection | None = None,
        *,
        parent: QObject | None = None,
        launch: Callable[[], bool] | None = None,
        companion: CompanionTools | None = None,
        load_targets: Callable[[], surfaces.ShareTargets] = surfaces.load_targets,
        send: Callable[..., surfaces.Outcome] = surfaces.send,
        pick_files: Callable[[str], list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus if bus is not None else QDBusConnection.sessionBus()
        self._launch = launch or launch_qt_client
        self._watchers: set[QDBusPendingCallWatcher] = set()
        self._daemon_owned: bool | None = None
        self.status: BackendStatus | None = None
        self.status_raw: dict | None = None
        self.error = ""
        self.unread = 0
        self.tether: TetherStatus | None = None
        self._base_icon = phone_icon()

        self.tray = QSystemTrayIcon(self)
        self.menu = QMenu()
        self.open_action = QAction(theme_icon("open"), _("Open BlueFerry"), self.menu)
        self.open_action.triggered.connect(self.open_app)
        self.audio_action = QAction(theme_icon("audio"), "", self.menu)
        self.audio_action.setCheckable(True)
        self.audio_action.triggered.connect(self._audio_triggered)
        self.hotspot_action = QAction(theme_icon("hotspot"), "", self.menu)
        self.hotspot_action.setCheckable(True)
        self.hotspot_action.triggered.connect(self._hotspot_triggered)
        self.mirror_action = QAction(theme_icon("mirror_notifications"), "", self.menu)
        self.mirror_action.setCheckable(True)
        self.mirror_action.triggered.connect(self._mirror_triggered)
        self.reconnect_action = QAction(theme_icon("reconnect"), "", self.menu)
        self.reconnect_action.triggered.connect(self._reconnect_triggered)
        # UxPlay, LocalSend and iPhone photos: started here, no daemon involved.
        self.companion = companion or CompanionTools(parent=self)
        self.companion.changed.connect(self.render_tools)
        self.companion.reported.connect(self._tool_reported)
        self.tool_actions: dict[str, QAction] = {}
        for key in ("mirror", "send", "photos", "eject", "pair"):
            action = QAction(theme_icon(key), "", self.menu)
            action.triggered.connect(lambda _checked=False, key=key: self.companion.run(key))
            self.tool_actions[key] = action
        # "Send to…": targets from plugins with the share capability
        # (PLUGINS.md 1.2), looked up off the UI thread when the menu opens.
        self._load_targets = load_targets
        self._send = send
        self._pick_files = pick_files or pick_files_dialog
        self._plugin_pool = QThreadPool(self)
        self._plugin_pool.setMaxThreadCount(1)
        self._plugin_tasks: set[Task] = set()
        self.share_targets: list[surfaces.ShareChoice] = []
        self._targets_loading = False
        self.share_menu = QMenu(presenter.share_menu_title(False, 0), self.menu)
        self.share_menu.setIcon(theme_icon("share"))
        self.quit_action = QAction(theme_icon("quit"), _("Quit"), self.menu)
        self.quit_action.triggered.connect(QApplication.quit)
        # Grouped like the phone card. Plasma's tray menus may show the
        # section titles only as separators; the order still groups them.
        self.menu.addAction(self.open_action)
        self.menu.setDefaultAction(self.open_action)
        self.menu.addAction(self.reconnect_action)
        self.menu.addSection(presenter.SECTION_QUICK)
        self.menu.addAction(self.audio_action)
        self.menu.addAction(self.hotspot_action)
        self.menu.addAction(self.mirror_action)
        self.menu.addSection(presenter.SECTION_TOOLS)
        for action in self.tool_actions.values():
            self.menu.addAction(action)
        self.share_action = self.menu.addMenu(self.share_menu)
        self.share_action.setVisible(False)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.menu.aboutToShow.connect(self.refresh_share_targets)
        # Tools and USB devices change without a signal: look when opened.
        self.menu.aboutToShow.connect(self.companion.refresh)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DELAY_MS)
        self._refresh_timer.timeout.connect(self.refresh)
        self._threads_timer = QTimer(self)
        self._threads_timer.setSingleShot(True)
        self._threads_timer.setInterval(REFRESH_DELAY_MS)
        self._threads_timer.timeout.connect(self.refresh_threads)

        self._service_watcher = QDBusServiceWatcher(
            BUS_NAME, self._bus,
            QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange, self,
        )
        self._service_watcher.serviceOwnerChanged.connect(self._owner_changed)
        for interface, name, slot in (
            (EVENTS_IFACE, "StatusChanged", "_status_invalidated()"),
            (EVENTS_IFACE, "HistoryChanged", "_history_invalidated(QVariantMap)"),
            (TETHER_IFACE, "TetherChanged", "_status_invalidated()"),
        ):
            self._bus.connect(BUS_NAME, OBJECT_PATH, interface, name, self, SLOT(slot))
        self.render()

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        # Shown once refresh() knows BlueFerry is set up (running or
        # activatable); an autostarted tray without a setup stays hidden.
        self.refresh()

    def close(self) -> None:
        self.tray.hide()
        self.menu.deleteLater()

    # ---- D-Bus ----------------------------------------------------------

    def _daemon_running(self) -> bool:
        # One synchronous lookup at start; afterwards the service watcher
        # keeps the answer current, so refreshes never block on the bus.
        if self._daemon_owned is None:
            self._daemon_owned = name_registered(self._bus, BUS_NAME)
        return self._daemon_owned

    def _call(
        self,
        service: str,
        interface: str,
        method: str,
        args: list,
        on_reply: Reply,
        on_error: Failure,
        *,
        timeout: int = CALL_TIMEOUT_MS,
        path: str = OBJECT_PATH,
    ) -> None:
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(args)
        # Never let a tray refresh activate a stopped daemon or client.
        message.setAutoStartService(False)
        watcher = QDBusPendingCallWatcher(self._bus.asyncCall(message, timeout), self)
        self._watchers.add(watcher)

        def finished(done: QDBusPendingCallWatcher) -> None:
            self._watchers.discard(done)
            done.deleteLater()
            if done.isError():
                on_error(done.error().name() or "")
            else:
                on_reply(done.reply().arguments())

        watcher.finished.connect(finished)

    @Slot()
    def _status_invalidated(self) -> None:
        self._refresh_timer.start()

    @Slot("QVariantMap")
    def _history_invalidated(self, _revision) -> None:
        self._threads_timer.start()

    def _owner_changed(self, _name: str, _old: str, new: str) -> None:
        self._daemon_owned = bool(new)
        self._refresh_timer.start()

    @Slot()
    def refresh(self) -> None:
        if not self._daemon_running():
            self.apply_offline()
            self._check_installed()
            return
        self.set_present(True)
        self._call(
            BUS_NAME, MESSAGES_IFACE, "GetStatus", [],
            lambda args: self.apply_status(args[0] if args else ""),
            lambda _name: self.apply_offline(),
        )

    def _check_installed(self) -> None:
        """Hide the icon unless the bus could start the BlueFerry service."""
        if not self._bus.isConnected():
            self.set_present(True)  # cannot tell; keep the way back in
            return

        def listed(args: list) -> None:
            names = args[0] if args else []
            self.set_present(BUS_NAME in [str(name) for name in names or []])

        self._call(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "ListActivatableNames", [],
            # Unknown: keep the icon rather than lose the only way back in.
            listed, lambda _name: self.set_present(True),
            path="/org/freedesktop/DBus",
        )

    def set_present(self, present: bool) -> None:
        """Show the icon for a usable BlueFerry setup, hide it otherwise."""
        self.tray.setVisible(present)

    @Slot()
    def refresh_threads(self) -> None:
        if self.status is None or not self._daemon_running():
            return
        self._call(
            # The signature says "u", but PySide6 can only marshal a Python
            # int as int32: QDBusArgument has no unsigned overload and
            # QVariant is not exposed. dbus-python does not enforce incoming
            # signatures, and the daemon bounds the limit anyway.
            BUS_NAME, MESSAGES_IFACE, "ListThreads", [THREAD_LIMIT],
            lambda args: self.apply_threads(args[0] if args else "[]"),
            lambda _name: None,
        )

    def refresh_tether(self) -> None:
        def failed(_name: str) -> None:
            self.tether = None
            self.render()

        self._call(
            BUS_NAME, TETHER_IFACE, "GetState", [],
            lambda args: self.apply_tether(args[0] if args else ""), failed,
        )

    # ---- state ----------------------------------------------------------

    def apply_offline(self) -> None:
        self.status = None
        self.status_raw = None
        self.error = ""
        self.unread = 0
        self.tether = None
        self.render()

    def apply_status(self, payload: object) -> None:
        try:
            raw = decode_mapping(payload)
        except ValueError:
            self.apply_offline()
            return
        incompatible = backend_compatibility_error(raw)
        if incompatible:
            self.apply_offline()
            self.error = _("The BlueFerry service needs an update.")
            self.render()
            return
        first = self.status is None
        self.status_raw = raw
        self.status = BackendStatus.from_dict(raw)
        self.error = ""
        self.render()
        self.refresh_tether()
        if first:
            self.refresh_threads()

    def apply_threads(self, payload: object) -> None:
        try:
            threads = decode_threads(payload)
        except ValueError:
            return
        self.unread = presenter.unread_total(threads)
        self.render()

    def apply_tether(self, payload: object) -> None:
        try:
            self.tether = TetherStatus.from_dict(decode_mapping(payload))
        except ValueError:
            self.tether = None
        self.render()

    def render(self) -> None:
        connected = self.status is not None and self.status.map
        self.tray.setIcon(badged_icon(
            self._base_icon, presenter.badge_text(self.unread), dimmed=not connected,
        ))
        self.tray.setToolTip(presenter.tooltip(self.status, self.unread, self.error))
        for action, toggle in (
            (self.audio_action, presenter.audio_toggle(self.status_raw)),
            (self.hotspot_action, presenter.hotspot_toggle(self.tether)),
            (self.mirror_action, presenter.mirror_toggle(self.status_raw)),
        ):
            action.setText(toggle.text)
            action.setVisible(toggle.visible)
            action.setEnabled(toggle.enabled)
            action.setChecked(toggle.checked)
        reconnect = presenter.reconnect_entry(self.status_raw)
        self.reconnect_action.setText(reconnect.text)
        self.reconnect_action.setVisible(reconnect.visible)
        self.reconnect_action.setEnabled(reconnect.enabled)
        self.render_tools()

    def render_tools(self) -> None:
        entries = presenter.tool_entries(
            self.companion.snapshot.tools, busy=self.companion.busy,
            needs_pairing=self.companion.needs_pairing,
        )
        shown = {entry.key for entry in entries}
        for entry in entries:
            action = self.tool_actions[entry.key]
            action.setText(entry.text)
            action.setVisible(entry.visible)
            action.setEnabled(entry.enabled)
        for key, action in self.tool_actions.items():
            if key not in shown:
                action.setVisible(False)

    # ---- Send to… (plugins) -------------------------------------------------

    def _plugin_work(
        self, work: Callable[[], object], done: Callable[[object], None],
        failed: Callable[[str], None],
    ) -> None:
        task = Task(work)
        self._plugin_tasks.add(task)
        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        task.signals.finished.connect(lambda: self._plugin_tasks.discard(task))
        self._plugin_pool.start(task)

    def refresh_share_targets(self) -> None:
        if self._targets_loading:
            return
        self._targets_loading = True
        self._plugin_work(self._load_targets, self._targets_done,
                          lambda _message: self._targets_done(None))

    def _targets_done(self, value: object) -> None:
        self._targets_loading = False
        if isinstance(value, surfaces.ShareTargets):
            self.share_targets = list(value.choices)
        self.render_share()

    def render_share(self) -> None:
        self.share_menu.clear()
        for choice in self.share_targets:
            action = self.share_menu.addAction(
                QIcon.fromTheme(choice.icon or "document-send"),
                surfaces.choice_label(choice, self.share_targets),
            )
            action.triggered.connect(lambda _checked=False, key=choice.key: self.send_to(key))
        self.share_menu.setTitle(presenter.share_menu_title(
            self._targets_loading, len(self.share_targets)))
        self.share_action.setVisible(bool(self.share_targets))

    def send_to(self, key: str) -> None:
        choice = next((c for c in self.share_targets if c.key == key), None)
        if choice is None:
            return
        paths = self._pick_files(choice.label)
        if not paths:
            return

        def done(outcome: object) -> None:
            if isinstance(outcome, surfaces.Outcome):
                self._tool_reported(outcome.ok, outcome.message)

        self._plugin_work(
            lambda: self._send(choice, paths), done,
            lambda message: self._tool_reported(
                False, message or _("The plugin did not take the files.")),
        )

    def _tool_reported(self, ok: bool, message: str) -> None:
        self.tray.showMessage(
            "BlueFerry", message,
            QSystemTrayIcon.MessageIcon.Information if ok else QSystemTrayIcon.MessageIcon.Warning,
            8000,
        )

    # ---- actions --------------------------------------------------------

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_app()

    @Slot()
    def open_app(self) -> None:
        """Raise a running Qt client, or start one."""
        if name_registered(self._bus, QT_CLIENT.bus_name):
            self._call(
                QT_CLIENT.bus_name, ACTIVATION_INTERFACE, "OpenMessage",
                # On Wayland the running window may only take focus with the
                # activation token Plasma handed to the tray for this click.
                ["", QProcessEnvironment.systemEnvironment().value("XDG_ACTIVATION_TOKEN")],
                lambda _args: None, lambda _name: self._launch(),
                path=ACTIVATION_PATH,
            )
            return
        self._launch()

    def _audio_triggered(self, checked: bool) -> None:
        self._action(MEDIA_IFACE, "SetPhoneAudioRoute", [presenter.audio_route_for(checked)])

    def _hotspot_triggered(self, checked: bool) -> None:
        self._action(TETHER_IFACE, "Connect" if checked else "Disconnect", [])

    def _mirror_triggered(self, checked: bool) -> None:
        self._action(MESSAGES_IFACE, "SetMirrorNotificationRemovals", [bool(checked)])

    def _reconnect_triggered(self) -> None:
        def replied(args: list) -> None:
            result = str(args[0]) if args else ""
            if result != "started":
                self.tray.showMessage(
                    "BlueFerry", result_text(result),
                    QSystemTrayIcon.MessageIcon.Warning
                    if result in ("unreachable", "bluez-kernel", "bluez-unresponsive")
                    else QSystemTrayIcon.MessageIcon.Information,
                    8000,
                )
            self._refresh_timer.start()

        def failed(name: str) -> None:
            self.tray.showMessage(
                "BlueFerry", reconnect_error_text(name), QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )

        self._call(
            BUS_NAME, MESSAGES_IFACE, "ReconnectPhone", [], replied, failed,
            timeout=ACTION_TIMEOUT_MS,
        )

    def _action(self, interface: str, method: str, args: list) -> None:
        def failed(name: str) -> None:
            if is_rate_limited(name):
                text = RATE_LIMITED_TEXT
            elif method == "SetPhoneAudioRoute" and name.endswith(".NotReady"):
                text = _(
                    "Bluetooth is still changing the iPhone's audio connection. "
                    "Try again shortly; if this keeps happening, use Reconnect."
                )
            else:
                text = _("The iPhone did not accept the change.")
            self.tray.showMessage(
                "BlueFerry", text, QSystemTrayIcon.MessageIcon.Warning, 5000,
            )
            self._refresh_timer.start()

        # Show the confirmed state, not the optimistic click.
        self.render()
        self._call(
            BUS_NAME, interface, method, args,
            lambda _args: self._refresh_timer.start(), failed,
            timeout=ACTION_TIMEOUT_MS,
        )


def pick_files_dialog(target: str) -> list[str]:
    files, _filter = QFileDialog.getOpenFileNames(
        None, _("Send to {target}").format(target=target),
    )
    return [str(path) for path in files]


def launch_qt_client() -> bool:
    program = shutil.which("blueferry-qt")
    if program:
        return bool(QProcess.startDetached(program, [])[0])
    return bool(QProcess.startDetached(sys.executable, ["-m", "blueferry.qt.app"])[0])


def name_registered(bus: QDBusConnection, name: str) -> bool:
    """Whether ``name`` has an owner; this never activates the service."""
    interface = bus.interface() if bus.isConnected() else None
    if interface is None:
        return False
    reply = interface.isServiceRegistered(name)
    return bool(reply.isValid() and reply.value())


def main() -> int:
    application = QApplication([sys.argv[0], *sys.argv[1:]])
    application.setApplicationName("blueferry-tray")
    application.setApplicationDisplayName("BlueFerry")
    application.setDesktopFileName("blueferry-tray")
    application.setQuitOnLastWindowClosed(False)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("blueferry-tray: no session bus", file=sys.stderr)
        return 1
    if not bus.registerService(TRAY_BUS_NAME):
        return 0  # another tray is already running
    controller = TrayController(bus, parent=application)
    controller.start()
    signal.signal(signal.SIGINT, lambda *_args: application.quit())
    signal.signal(signal.SIGTERM, lambda *_args: application.quit())
    # Let Python see signals while Qt's loop runs.
    ticker = QTimer(application)
    ticker.start(500)
    ticker.timeout.connect(lambda: None)
    exit_code = application.exec()
    controller.close()
    bus.unregisterService(TRAY_BUS_NAME)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
