import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
QT_PACKAGES = PROJECT_ROOT / ".qt_packages"
if QT_PACKAGES.is_dir():
    sys.path.insert(0, str(QT_PACKAGES))

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("身后有人提醒")
        self.setMinimumSize(620, 460)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.process_finished)
        self.latest_capture = None
        self.build_ui()
        self.refresh_windows()

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

        run_row = QHBoxLayout()
        self.status_label = QLabel("未启动")
        self.status_label.setObjectName("status")
        run_row.addWidget(self.status_label)
        run_row.addStretch()
        self.start_button = QPushButton("开始监测")
        self.start_button.clicked.connect(self.toggle_monitoring)
        run_row.addWidget(self.start_button)
        self.capture_button = QPushButton("查看截图")
        self.capture_button.setObjectName("secondary")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.open_latest_capture)
        run_row.addWidget(self.capture_button)
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
            "--no-preview",
        ]
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
                self.capture_button.setEnabled(self.latest_capture.is_file())
            elif line.startswith("Triggered:"):
                self.status_label.setText("已切换到目标应用")
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

    def open_latest_capture(self):
        if self.latest_capture and self.latest_capture.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.latest_capture)))

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
