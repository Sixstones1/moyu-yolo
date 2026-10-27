import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
QT_PACKAGES = PROJECT_ROOT / ".qt_packages"
if QT_PACKAGES.is_dir():
    sys.path.insert(0, str(QT_PACKAGES))

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from winotify import Notification

from detect_camera import activate_window, list_window_records


ROOT = PROJECT_ROOT
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
DETECTOR = ROOT / "detect_camera.py"


class BorderFlash(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowOpacity(0.9)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#e32636"), 8))
        painter.drawRect(self.rect().adjusted(4, 4, -5, -5))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("身后有人提醒")
        self.setMinimumSize(760, 620)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.process_finished)
        self.latest_capture = None
        self.alert_windows = []
        self.build_ui()
        self.refresh_windows()
        self.refresh_captures()

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        heading = QLabel("身后有人提醒")
        heading.setObjectName("heading")
        subtitle = QLabel("检测到有人经过时，切换到你选择的应用")
        subtitle.setObjectName("muted")
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        target_group = QFrame()
        target_group.setObjectName("section")
        target_layout = QGridLayout(target_group)
        target_layout.setContentsMargins(18, 16, 18, 16)
        target_layout.setHorizontalSpacing(12)
        target_layout.setVerticalSpacing(12)
        target_layout.addWidget(QLabel("切换目标"), 0, 0)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(360)
        target_layout.addWidget(self.window_combo, 0, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_windows)
        target_layout.addWidget(self.refresh_button, 0, 2)
        target_layout.addWidget(QLabel("摄像头"), 1, 0)
        self.camera_spin = QSpinBox()
        self.camera_spin.setRange(0, 10)
        self.camera_spin.setValue(0)
        target_layout.addWidget(self.camera_spin, 1, 1)
        self.test_button = QPushButton("测试切换")
        self.test_button.setObjectName("secondary")
        self.test_button.clicked.connect(self.test_switch)
        target_layout.addWidget(self.test_button, 1, 2)
        layout.addWidget(target_group)

        settings_group = QFrame()
        settings_group.setObjectName("section")
        settings_layout = QGridLayout(settings_group)
        settings_layout.setContentsMargins(18, 16, 18, 16)
        settings_layout.setHorizontalSpacing(12)
        settings_layout.setVerticalSpacing(10)
        settings_layout.addWidget(QLabel("连续触发帧数"), 0, 0)
        self.trigger_frames_spin = QSpinBox()
        self.trigger_frames_spin.setRange(1, 120)
        self.trigger_frames_spin.setValue(6)
        settings_layout.addWidget(self.trigger_frames_spin, 0, 1)
        settings_layout.addWidget(QLabel("最小移动像素"), 0, 2)
        self.movement_pixels_spin = QSpinBox()
        self.movement_pixels_spin.setRange(1, 500)
        self.movement_pixels_spin.setValue(18)
        settings_layout.addWidget(self.movement_pixels_spin, 0, 3)
        settings_layout.addWidget(QLabel("YOLO 置信度"), 1, 0)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.05, 0.95)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setDecimals(2)
        self.confidence_spin.setValue(0.35)
        settings_layout.addWidget(self.confidence_spin, 1, 1)
        settings_layout.addWidget(QLabel("冷却秒数"), 1, 2)
        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0, 300)
        self.cooldown_spin.setSingleStep(1)
        self.cooldown_spin.setDecimals(1)
        self.cooldown_spin.setValue(10)
        settings_layout.addWidget(self.cooldown_spin, 1, 3)
        settings_layout.addWidget(QLabel("背景区域底边"), 2, 0)
        self.zone_bottom_spin = QDoubleSpinBox()
        self.zone_bottom_spin.setRange(0.1, 1.0)
        self.zone_bottom_spin.setSingleStep(0.05)
        self.zone_bottom_spin.setDecimals(2)
        self.zone_bottom_spin.setValue(0.72)
        settings_layout.addWidget(self.zone_bottom_spin, 2, 1)
        self.look_trigger_check = QCheckBox("正脸辅助触发")
        self.look_trigger_check.setToolTip("默认关闭，避免你看屏幕时被误判为触发")
        settings_layout.addWidget(self.look_trigger_check, 2, 2)
        self.notification_check = QCheckBox("Windows 通知")
        self.notification_check.setToolTip("系统通知可能有延迟，默认关闭")
        settings_layout.addWidget(self.notification_check, 2, 3)
        layout.addWidget(settings_group)

        capture_group = QFrame()
        capture_group.setObjectName("section")
        capture_layout = QGridLayout(capture_group)
        capture_layout.setContentsMargins(18, 12, 18, 12)
        capture_layout.setHorizontalSpacing(12)
        capture_layout.addWidget(QLabel("触发截图历史"), 0, 0)
        self.capture_combo = QComboBox()
        self.capture_combo.setMinimumWidth(470)
        self.capture_combo.activated.connect(self.open_selected_capture)
        capture_layout.addWidget(self.capture_combo, 0, 1)
        self.refresh_capture_button = QPushButton("刷新")
        self.refresh_capture_button.setObjectName("secondary")
        self.refresh_capture_button.clicked.connect(lambda: self.refresh_captures())
        capture_layout.addWidget(self.refresh_capture_button, 0, 2)
        self.capture_button = QPushButton("打开")
        self.capture_button.setObjectName("secondary")
        self.capture_button.clicked.connect(self.open_selected_capture)
        capture_layout.addWidget(self.capture_button, 0, 3)
        layout.addWidget(capture_group)

        run_row = QHBoxLayout()
        self.status_label = QLabel("未启动")
        self.status_label.setObjectName("status")
        run_row.addWidget(self.status_label)
        run_row.addStretch()
        self.start_button = QPushButton("开始监测")
        self.start_button.clicked.connect(self.toggle_monitoring)
        run_row.addWidget(self.start_button)
        layout.addLayout(run_row)

        log_label = QLabel("运行记录")
        log_label.setObjectName("section-title")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("检测状态和切换结果会显示在这里")
        self.log.document().setMaximumBlockCount(300)
        layout.addWidget(log_label)
        layout.addWidget(self.log, 1)
        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f4f6f8; color: #20272e; }
            QLabel { font-size: 14px; }
            QLabel#heading { font-size: 24px; font-weight: 650; }
            QLabel#muted { color: #63717d; }
            QLabel#section-title { font-weight: 600; }
            QLabel#status { color: #495761; font-weight: 600; }
            QFrame#section { background: white; border: 1px solid #dce2e7; border-radius: 6px; }
            QComboBox, QSpinBox, QPlainTextEdit {
                background: white; border: 1px solid #ccd5dc; border-radius: 4px;
                padding: 8px; selection-background-color: #3574a5;
            }
            QPushButton {
                background: #246b4a; color: white; border: 0; border-radius: 4px;
                padding: 9px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #1d593d; }
            QPushButton#secondary { background: #e6edf2; color: #24323c; }
            QPushButton#secondary:hover { background: #d8e2e9; }
            QPushButton:disabled { background: #aeb8bf; color: #edf0f2; }
            QPlainTextEdit { font-family: Consolas, monospace; font-size: 12px; }
            """
        )

    def refresh_windows(self):
        current_hwnd = self.window_combo.currentData()
        current = self.window_combo.currentText()
        self.window_combo.clear()
        try:
            windows = [
                (hwnd, title)
                for hwnd, title in list_window_records()
                if title != self.windowTitle()
            ]
        except Exception as error:
            self.log.appendPlainText(f"读取窗口列表失败：{error}")
            return
        for hwnd, title in windows:
            self.window_combo.addItem(title, hwnd)
        index = self.window_combo.findData(current_hwnd)
        if index < 0:
            index = self.window_combo.findText(current, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            self.window_combo.setCurrentIndex(index)
        if not windows:
            self.status_label.setText("没有找到可切换的窗口")

    def test_switch(self):
        title = self.window_combo.currentText()
        if not title:
            QMessageBox.warning(self, "没有目标窗口", "请先刷新并选择一个已打开的应用窗口。")
            return
        activated = activate_window(title, hwnd=self.window_combo.currentData())
        if activated:
            self.log.appendPlainText(f"手动测试成功：已切换到 {activated}")
            self.status_label.setText("测试切换成功")
        else:
            self.log.appendPlainText(f"手动测试失败：Windows 未允许激活 {title}")
            self.status_label.setText("测试切换失败")

    def toggle_monitoring(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
            return
        if not PYTHONW.is_file() or not DETECTOR.is_file():
            QMessageBox.critical(self, "项目环境不完整", f"找不到运行文件：\n{PYTHONW}")
            return
        title = self.window_combo.currentText()
        if not title:
            QMessageBox.warning(self, "没有目标窗口", "请先刷新并选择目标应用。")
            return
        args = [
            "-u",
            str(DETECTOR),
            "--camera",
            str(self.camera_spin.value()),
            "--ide-title",
            title,
            "--ide-hwnd",
            str(self.window_combo.currentData() or 0),
            "--confidence",
            str(self.confidence_spin.value()),
            "--trigger-frames",
            str(self.trigger_frames_spin.value()),
            "--movement-pixels",
            str(self.movement_pixels_spin.value()),
            "--cooldown",
            str(self.cooldown_spin.value()),
            "--zone-bottom",
            str(self.zone_bottom_spin.value()),
            "--no-preview",
        ]
        if self.look_trigger_check.isChecked():
            args.append("--look-trigger")
        self.process.setProgram(str(PYTHONW))
        self.process.setArguments(args)
        self.process.start()
        if not self.process.waitForStarted(3000):
            QMessageBox.critical(self, "启动失败", self.process.errorString())
            return
        self.start_button.setText("停止监测")
        self.status_label.setText("正在启动摄像头和模型…")
        self.test_button.setEnabled(False)
        self.append_log(f"开始监测；目标窗口：{title}")

    def read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            self.append_log(line)
            if line.startswith("Capture saved: "):
                self.latest_capture = Path(line.removeprefix("Capture saved: ").strip())
                self.refresh_captures(self.latest_capture)
            elif line.startswith("Trigger detected:"):
                self.status_label.setText("检测到触发，正在切换")
                self.show_border_flash()
            elif line.startswith("Triggered:"):
                self.status_label.setText("已切换到目标应用")
                if self.notification_check.isChecked():
                    self.send_private_notification()

    def read_error(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            self.append_log(line)

    def process_finished(self, _exit_code, _exit_status):
        self.start_button.setText("开始监测")
        self.test_button.setEnabled(True)
        if self.status_label.text() not in ("已切换到目标应用",):
            self.status_label.setText("监测已停止")
        self.append_log("监测进程已停止")

    def append_log(self, text):
        self.log.appendPlainText(text)

    def send_private_notification(self):
        try:
            Notification(
                app_id="System Status",
                title="状态更新",
                msg="请确认周围情况",
            ).show()
            self.append_log("Windows 通知已发送")
        except Exception as error:
            self.append_log(f"Windows 通知发送失败：{error}")

    def refresh_captures(self, select_path=None):
        capture_dir = ROOT / "captures"
        paths = sorted(
            (path for path in capture_dir.glob("*.jpg") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        self.capture_combo.blockSignals(True)
        self.capture_combo.clear()
        for path in paths:
            self.capture_combo.addItem(path.name, str(path))
        self.capture_combo.blockSignals(False)
        if not paths:
            self.capture_button.setEnabled(False)
            self.latest_capture = None
            return
        self.capture_button.setEnabled(True)
        selected = Path(select_path) if select_path else paths[0]
        index = self.capture_combo.findData(str(selected))
        if index < 0:
            index = 0
        self.capture_combo.setCurrentIndex(index)
        self.latest_capture = Path(self.capture_combo.currentData())

    def open_selected_capture(self, _index=None):
        path = self.capture_combo.currentData()
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_border_flash(self):
        for screen in QApplication.screens():
            border = BorderFlash()
            border.setGeometry(screen.geometry())
            border.show()
            border.raise_()
            self.alert_windows.append(border)
            QTimer.singleShot(900, lambda widget=border: self.hide_border_flash(widget))

    def hide_border_flash(self, widget):
        widget.hide()
        widget.deleteLater()
        if widget in self.alert_windows:
            self.alert_windows.remove(widget)

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
