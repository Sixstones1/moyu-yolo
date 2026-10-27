import ctypes
import json
import sys
import ctypes.wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
QT_PACKAGES = PROJECT_ROOT / ".qt_packages"
if QT_PACKAGES.is_dir():
    sys.path.insert(0, str(QT_PACKAGES))

from PySide6.QtCore import QProcess, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QSpinBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QImage, QPixmap
import cv2
from winotify import Notification

from detect_camera import activate_window, list_window_records
from face_guard import FaceGuard, DEFAULT_PROFILE_PATH


ROOT = PROJECT_ROOT
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
DETECTOR = ROOT / "detect_camera.py"
DEBUG_URL = "http://127.0.0.1:8765/"
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
HOTKEY_SWITCH = 1
HOTKEY_GLASS = 2


def parse_global_hotkey(sequence):
    text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if not text:
        raise ValueError("快捷键不能为空")
    parts = [part.strip().casefold() for part in text.split("+")]
    key_name = parts[-1]
    modifiers = 0
    for modifier in parts[:-1]:
        if modifier in ("ctrl", "control"):
            modifiers |= 0x0002
        elif modifier == "alt":
            modifiers |= 0x0001
        elif modifier == "shift":
            modifiers |= 0x0004
        elif modifier in ("meta", "win"):
            modifiers |= 0x0008
        else:
            raise ValueError(f"不支持的快捷键修饰键：{modifier}")
    special_keys = {
        "scroll lock": 0x91,
        "pause": 0x13,
        "escape": 0x1B,
        "space": 0x20,
        "tab": 0x09,
        "enter": 0x0D,
        "delete": 0x2E,
        "insert": 0x2D,
        "home": 0x24,
        "end": 0x23,
        "page up": 0x21,
        "page down": 0x22,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
    }
    if key_name in special_keys:
        key = special_keys[key_name]
    elif len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
        key = ord(key_name.upper())
    elif key_name.startswith("f") and key_name[1:].isdigit() and 1 <= int(key_name[1:]) <= 24:
        key = 0x6F + int(key_name[1:])
    else:
        raise ValueError(f"不支持的快捷键：{text}")
    return modifiers | MOD_NOREPEAT, key


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
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowOpacity(0.9)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#e32636"), 8))
        painter.drawRect(self.rect().adjusted(4, 4, -5, -5))


class GlassOverlay(QWidget):
    def __init__(self, opacity):
        super().__init__()
        self.opacity = opacity
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(22, 28, 36, int(self.opacity * 255)))


class EnrollmentDialog(QDialog):
    def __init__(self, camera_index, face_guard, parent=None):
        super().__init__(parent)
        self.setWindowTitle("登记本人")
        self.setMinimumWidth(540)
        self.face_guard = face_guard
        self.frames = []
        self.latest_frame = None
        self.camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not self.camera.isOpened():
            self.camera.release()
            raise RuntimeError(f"无法打开摄像头 {camera_index}")

        layout = QVBoxLayout(self)
        self.preview = QLabel("正在连接摄像头…")
        self.preview.setMinimumSize(480, 270)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        self.instruction = QLabel()
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)
        self.capture_button = QPushButton("采集当前姿势")
        self.capture_button.clicked.connect(self.capture_sample)
        layout.addWidget(self.capture_button)
        self.update_instruction()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_preview)
        self.timer.start(33)

    def update_instruction(self):
        pose = ("正视摄像头", "向左转头", "向右转头")[
            min(len(self.frames) // 2, 2)
        ]
        self.instruction.setText(
            f"请{pose}，采集 {len(self.frames)}/6 张。"
            "每个姿势采集两张；画面中必须只有你一人。"
        )
        if len(self.frames) >= 6:
            self.capture_button.setText("完成人脸登记")

    def update_preview(self):
        ok, frame = self.camera.read()
        if not ok:
            self.instruction.setText("读取摄像头画面失败，请检查摄像头连接。")
            return
        self.latest_frame = frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def capture_sample(self):
        if len(self.frames) >= 6:
            self.accept()
            return
        if self.latest_frame is None:
            return
        if len(self.face_guard.detect(self.latest_frame)) != 1:
            QMessageBox.warning(self, "无法采样", "画面中必须恰好检测到一张脸。")
            return
        self.frames.append(self.latest_frame.copy())
        self.update_instruction()
        if len(self.frames) == 6:
            self.capture_button.setText("完成人脸登记")

    def done(self, result):
        self.timer.stop()
        self.camera.release()
        super().done(result)

    def closeEvent(self, event):
        self.timer.stop()
        self.camera.release()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("身后有人提醒")
        self.setMinimumSize(840, 700)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.process_finished)
        self.settings = QSettings("MoyuYolo", "MoyuYolo")
        self.latest_capture = None
        self.alert_windows = []
        self.glass_overlays = {}
        self.switch_enabled = True
        self.glass_enabled = True
        self.protection_absent = False
        self.protection_return_mode = "both"
        self.current_screen_mode = "both"
        self.registered_hotkeys = {}
        self.face_guard = None
        self.build_ui()
        self.refresh_windows()
        self.refresh_captures()
        self.register_hotkeys()
        for screen in QApplication.screens():
            screen.geometryChanged.connect(self.refresh_overlay_geometry)
        QApplication.instance().screenAdded.connect(self.on_screen_configuration_changed)
        QApplication.instance().screenRemoved.connect(self.on_screen_configuration_changed)

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(3)
        heading = QLabel("身后有人提醒")
        heading.setObjectName("heading")
        subtitle = QLabel("检测到多人出现时，快速切回指定应用")
        subtitle.setObjectName("muted")
        title_stack.addWidget(heading)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.status_label = QLabel("未启动")
        self.status_label.setObjectName("status")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        target_group = QGroupBox("目标应用与摄像头")
        target_layout = QGridLayout(target_group)
        target_layout.setContentsMargins(16, 18, 16, 14)
        target_layout.setHorizontalSpacing(10)
        target_layout.setVerticalSpacing(9)
        target_layout.addWidget(QLabel("切换到"), 0, 0)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(350)
        target_layout.addWidget(self.window_combo, 0, 1)
        self.show_all_windows_check = QCheckBox("选择其他应用")
        self.show_all_windows_check.toggled.connect(self.refresh_windows)
        target_layout.addWidget(self.show_all_windows_check, 2, 1, 1, 2)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("secondary")
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

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(True)
        layout.addWidget(self.settings_tabs, 1)

        detect_page = QWidget()
        detect_layout = QGridLayout(detect_page)
        detect_layout.setContentsMargins(8, 14, 8, 8)
        detect_layout.setHorizontalSpacing(12)
        detect_layout.setVerticalSpacing(12)
        trigger_group = QGroupBox("触发条件")
        trigger_layout = QFormLayout(trigger_group)
        trigger_layout.setContentsMargins(16, 22, 16, 14)
        trigger_layout.setVerticalSpacing(12)
        self.trigger_frames_spin = QSpinBox()
        self.trigger_frames_spin.setRange(1, 120)
        self.trigger_frames_spin.setValue(6)
        self.trigger_frames_spin.setSuffix(" 帧")
        trigger_layout.addRow("连续满足人数", self.trigger_frames_spin)
        self.minimum_people_spin = QSpinBox()
        self.minimum_people_spin.setRange(2, 20)
        self.minimum_people_spin.setValue(2)
        self.minimum_people_spin.setSuffix(" 人")
        trigger_layout.addRow("触发最低人数", self.minimum_people_spin)
        trigger_note = QLabel("画面中至少两人连续达到设定帧数即保存截图并触发。")
        trigger_note.setObjectName("note")
        trigger_note.setWordWrap(True)
        trigger_layout.addRow(trigger_note)
        detect_layout.addWidget(trigger_group, 0, 0)

        sensitivity_group = QGroupBox("检测灵敏度与提醒")
        sensitivity_layout = QFormLayout(sensitivity_group)
        sensitivity_layout.setContentsMargins(16, 22, 16, 14)
        sensitivity_layout.setVerticalSpacing(12)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.05, 0.95)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setDecimals(2)
        self.confidence_spin.setValue(0.35)
        sensitivity_layout.addRow("YOLO 置信度", self.confidence_spin)
        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0, 300)
        self.cooldown_spin.setSingleStep(1)
        self.cooldown_spin.setDecimals(1)
        self.cooldown_spin.setValue(10)
        self.cooldown_spin.setSuffix(" 秒")
        sensitivity_layout.addRow("触发冷却时间", self.cooldown_spin)
        self.look_trigger_check = QCheckBox("允许正脸辅助触发")
        self.look_trigger_check.setToolTip("默认关闭，避免画面中的人看向摄像头时误触发")
        sensitivity_layout.addRow(self.look_trigger_check)
        self.notification_check = QCheckBox("发送 Windows 通知")
        self.notification_check.setToolTip("通知可能有系统延迟；关闭时仍保留屏幕红框提醒")
        sensitivity_layout.addRow(self.notification_check)
        detect_note = QLabel("数值越高，检测越严格；通知延迟可能受 Windows 通知中心影响。")
        detect_note.setObjectName("note")
        detect_note.setWordWrap(True)
        sensitivity_layout.addRow(detect_note)
        detect_layout.addWidget(sensitivity_group, 0, 1)
        detect_layout.setColumnStretch(0, 1)
        detect_layout.setColumnStretch(1, 1)
        detect_layout.setRowStretch(1, 1)
        self.settings_tabs.addTab(detect_page, "检测与提醒")

        display_page = QWidget()
        display_layout = QGridLayout(display_page)
        display_layout.setContentsMargins(8, 14, 8, 8)
        display_layout.setHorizontalSpacing(12)
        display_layout.setVerticalSpacing(12)
        head_group = QGroupBox("头部方向检测")
        head_layout = QFormLayout(head_group)
        head_layout.setContentsMargins(16, 22, 16, 14)
        head_layout.setVerticalSpacing(11)
        self.head_direction_check = QCheckBox("显示头部方向")
        self.head_direction_check.setToolTip("在运行记录中显示检测到的头部朝向")
        head_layout.addRow(self.head_direction_check)
        self.glass_check = QCheckBox("根据朝向切换显示器隐私遮罩")
        self.glass_check.setToolTip("启用后，按校准区域保持目标屏幕清晰")
        head_layout.addRow(self.glass_check)
        self.head_interval_spin = QSpinBox()
        self.head_interval_spin.setRange(1, 10)
        self.head_interval_spin.setValue(2)
        self.head_interval_spin.setSuffix(" 帧")
        head_layout.addRow("每隔多少帧检测", self.head_interval_spin)
        self.head_stability_spin = QSpinBox()
        self.head_stability_spin.setRange(1, 30)
        self.head_stability_spin.setValue(5)
        self.head_stability_spin.setSuffix(" 次")
        head_layout.addRow("切换前稳定次数", self.head_stability_spin)
        self.head_invert_check = QCheckBox("反转 yaw 左右方向")
        self.head_invert_check.setToolTip("摄像头方向导致校准左右相反时启用")
        head_layout.addRow(self.head_invert_check)
        head_note = QLabel("头部检测与 YOLO 共用摄像头画面，不会启动第二个检测进程。")
        head_note.setObjectName("note")
        head_note.setWordWrap(True)
        head_layout.addRow(head_note)
        display_layout.addWidget(head_group, 0, 0)

        mask_group = QGroupBox("显示器隐私遮罩与校准")
        mask_layout = QVBoxLayout(mask_group)
        mask_layout.setContentsMargins(16, 22, 16, 14)
        mask_layout.setSpacing(10)
        opacity_row = QHBoxLayout()
        opacity_label = QLabel("遮罩强度")
        self.glass_opacity_spin = QDoubleSpinBox()
        self.glass_opacity_spin.setRange(0.1, 1.0)
        self.glass_opacity_spin.setSingleStep(0.05)
        self.glass_opacity_spin.setValue(0.72)
        self.glass_opacity_spin.setSuffix(" ")
        opacity_row.addWidget(opacity_label)
        opacity_row.addStretch()
        opacity_row.addWidget(self.glass_opacity_spin)
        mask_layout.addLayout(opacity_row)
        calibration_note = QLabel(
            "三个 yaw 中心点、范围和实时测试在头向调试页中设置。"
        )
        calibration_note.setObjectName("note")
        calibration_note.setWordWrap(True)
        mask_layout.addWidget(calibration_note)
        self.debug_button = QPushButton("打开头向调试与校准")
        self.debug_button.setObjectName("secondary")
        self.debug_button.clicked.connect(self.open_debug_page)
        mask_layout.addWidget(self.debug_button)
        test_mask_row = QHBoxLayout()
        self.test_left_glass_button = QPushButton("测试遮罩左屏")
        self.test_left_glass_button.setObjectName("secondary")
        self.test_left_glass_button.clicked.connect(lambda: self.show_glass_for("right"))
        test_mask_row.addWidget(self.test_left_glass_button)
        self.test_right_glass_button = QPushButton("测试遮罩右屏")
        self.test_right_glass_button.setObjectName("secondary")
        self.test_right_glass_button.clicked.connect(lambda: self.show_glass_for("left"))
        test_mask_row.addWidget(self.test_right_glass_button)
        mask_layout.addLayout(test_mask_row)
        self.clear_glass_button = QPushButton("关闭所有遮罩")
        self.clear_glass_button.setObjectName("secondary")
        self.clear_glass_button.clicked.connect(lambda: self.show_glass_for("center"))
        mask_layout.addWidget(self.clear_glass_button)
        display_layout.addWidget(mask_group, 0, 1)

        direction_group = QGroupBox("方向日志阈值")
        direction_layout = QFormLayout(direction_group)
        direction_layout.setContentsMargins(16, 20, 16, 12)
        direction_layout.setHorizontalSpacing(18)
        direction_layout.setVerticalSpacing(8)
        self.head_left_spin = QDoubleSpinBox()
        self.head_left_spin.setRange(-90, 0)
        self.head_left_spin.setSingleStep(1)
        self.head_left_spin.setValue(-12)
        self.head_left_spin.setSuffix("°")
        direction_layout.addRow("判定为左转", self.head_left_spin)
        self.head_right_spin = QDoubleSpinBox()
        self.head_right_spin.setRange(0, 90)
        self.head_right_spin.setSingleStep(1)
        self.head_right_spin.setValue(12)
        self.head_right_spin.setSuffix("°")
        direction_layout.addRow("判定为右转", self.head_right_spin)
        direction_note = QLabel("只影响方向日志标签；屏幕亮灭区域以调试页的 yaw 校准为准。")
        direction_note.setObjectName("note")
        direction_note.setWordWrap(True)
        direction_layout.addRow(direction_note)
        display_layout.addWidget(direction_group, 1, 0, 1, 2)
        display_layout.setColumnStretch(0, 1)
        display_layout.setColumnStretch(1, 1)
        display_layout.setRowStretch(2, 1)
        self.settings_tabs.addTab(display_page, "头部与显示器")

        guard_page = QWidget()
        guard_layout = QVBoxLayout(guard_page)
        guard_layout.setContentsMargins(8, 14, 8, 8)
        guard_layout.setSpacing(12)
        face_group = QGroupBox("本人识别与离席保护")
        face_layout = QFormLayout(face_group)
        face_layout.setContentsMargins(16, 22, 16, 14)
        face_layout.setVerticalSpacing(12)
        self.face_status_label = QLabel(
            "已登记" if DEFAULT_PROFILE_PATH.is_file() else "尚未登记"
        )
        face_layout.addRow("本地人脸档案", self.face_status_label)
        enroll_row = QHBoxLayout()
        self.enroll_face_button = QPushButton("登记 / 重新登记")
        self.enroll_face_button.clicked.connect(self.enroll_face)
        enroll_row.addWidget(self.enroll_face_button)
        self.clear_face_button = QPushButton("删除档案")
        self.clear_face_button.setObjectName("secondary")
        self.clear_face_button.clicked.connect(self.clear_face_profile)
        enroll_row.addWidget(self.clear_face_button)
        enroll_row.addStretch()
        face_layout.addRow(enroll_row)
        self.absence_check = QCheckBox("未识别到本人时自动切换并遮住所有屏幕")
        face_layout.addRow(self.absence_check)
        self.absence_timeout_spin = QDoubleSpinBox()
        self.absence_timeout_spin.setRange(1, 60)
        self.absence_timeout_spin.setValue(5)
        self.absence_timeout_spin.setSuffix(" 秒")
        face_layout.addRow("离席确认时间", self.absence_timeout_spin)
        self.return_timeout_spin = QDoubleSpinBox()
        self.return_timeout_spin.setRange(0.5, 15)
        self.return_timeout_spin.setValue(1)
        self.return_timeout_spin.setSuffix(" 秒")
        face_layout.addRow("返回确认时间", self.return_timeout_spin)
        self.face_check_every_spin = QSpinBox()
        self.face_check_every_spin.setRange(1, 120)
        self.face_check_every_spin.setValue(15)
        self.face_check_every_spin.setSuffix(" 帧")
        face_layout.addRow("本人核验间隔", self.face_check_every_spin)
        self.face_threshold_spin = QDoubleSpinBox()
        self.face_threshold_spin.setRange(0.1, 0.9)
        self.face_threshold_spin.setDecimals(3)
        self.face_threshold_spin.setSingleStep(0.01)
        self.face_threshold_spin.setValue(0.363)
        face_layout.addRow("人脸相似度阈值", self.face_threshold_spin)
        face_note = QLabel(
            "仅保存本机人脸特征，不保存登记照片。登记时按提示正视及左右转头采样；"
            "检测到多张脸时不会采样。"
        )
        face_note.setObjectName("note")
        face_note.setWordWrap(True)
        face_layout.addRow(face_note)
        guard_layout.addWidget(face_group)

        hotkey_group = QGroupBox("全局快捷键")
        hotkey_layout = QFormLayout(hotkey_group)
        hotkey_layout.setContentsMargins(16, 22, 16, 14)
        hotkey_layout.setVerticalSpacing(12)
        self.switch_hotkey_edit = QKeySequenceEdit()
        self.switch_hotkey_edit.setKeySequence(
            QKeySequence(
                self.settings.value("switchHotkey", "Scroll Lock")
            )
        )
        hotkey_layout.addRow("暂停多人触发切换", self.switch_hotkey_edit)
        self.glass_hotkey_edit = QKeySequenceEdit()
        self.glass_hotkey_edit.setKeySequence(
            QKeySequence(self.settings.value("glassHotkey", "Pause"))
        )
        hotkey_layout.addRow("关闭 / 恢复隐私遮罩", self.glass_hotkey_edit)
        hotkey_note = QLabel(
            "默认 ScrLk 暂停切换但保留检测和截图；Pause 暂停遮罩。"
            "离席保护启用时会覆盖这两个手动暂停。"
        )
        hotkey_note.setObjectName("note")
        hotkey_note.setWordWrap(True)
        hotkey_layout.addRow(hotkey_note)
        self.hotkey_status_label = QLabel("全局快捷键尚未注册")
        hotkey_layout.addRow("状态", self.hotkey_status_label)
        self.switch_hotkey_edit.editingFinished.connect(self.register_hotkeys)
        self.glass_hotkey_edit.editingFinished.connect(self.register_hotkeys)
        guard_layout.addWidget(hotkey_group)
        guard_layout.addStretch()
        self.settings_tabs.addTab(guard_page, "本人保护与快捷键")

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(8, 14, 8, 8)
        history_layout.setSpacing(12)
        capture_group = QGroupBox("触发截图历史")
        capture_layout = QHBoxLayout(capture_group)
        capture_layout.setContentsMargins(14, 20, 14, 12)
        self.capture_combo = QComboBox()
        self.capture_combo.setMinimumWidth(300)
        self.capture_combo.activated.connect(self.open_selected_capture)
        capture_layout.addWidget(self.capture_combo, 1)
        self.refresh_capture_button = QPushButton("刷新列表")
        self.refresh_capture_button.setObjectName("secondary")
        self.refresh_capture_button.clicked.connect(lambda: self.refresh_captures())
        capture_layout.addWidget(self.refresh_capture_button)
        self.capture_button = QPushButton("打开截图")
        self.capture_button.setObjectName("secondary")
        self.capture_button.clicked.connect(self.open_selected_capture)
        capture_layout.addWidget(self.capture_button)
        history_layout.addWidget(capture_group)
        log_group = QGroupBox("运行记录")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(14, 20, 14, 12)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("检测状态和切换结果会显示在这里")
        self.log.document().setMaximumBlockCount(300)
        log_layout.addWidget(self.log)
        history_layout.addWidget(log_group, 1)
        self.settings_tabs.addTab(history_page, "历史与日志")

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_row.addStretch()
        self.start_button = QPushButton("开始监测")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.toggle_monitoring)
        run_row.addWidget(self.start_button)
        layout.addLayout(run_row)

        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f2f5f4; color: #202b30; }
            QLabel { font-size: 13px; }
            QLabel#heading { font-size: 24px; font-weight: 700; color: #183c37; }
            QLabel#muted, QLabel#note { color: #68777b; }
            QLabel#note { font-size: 12px; }
            QLabel#status {
                background: #e3eee8; color: #21634b; border-radius: 12px;
                padding: 6px 12px; font-weight: 600;
            }
            QGroupBox {
                background: #ffffff; border: 1px solid #d8e1de;
                border-radius: 6px; margin-top: 8px; padding-top: 7px;
                font-size: 14px; font-weight: 650;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; color: #31534b; }
            QTabWidget::pane { border: 0; }
            QTabBar::tab {
                background: #e6ecea; color: #50615d; padding: 9px 16px;
                margin-right: 4px; border-radius: 4px; font-weight: 600;
            }
            QTabBar::tab:selected { background: #ffffff; color: #176b58; }
            QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
                background: #ffffff; border: 1px solid #cbd6d2; border-radius: 4px;
                padding: 7px; selection-background-color: #277c69;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
                border: 1px solid #398d78;
            }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QPushButton {
                background: #e7edeb; color: #263b35; border: 1px solid #d5dfdb;
                border-radius: 4px; padding: 8px 12px; font-weight: 600;
            }
            QPushButton:hover { background: #dce7e3; }
            QPushButton#primary { background: #1d765f; color: white; border: 0; padding: 10px 22px; }
            QPushButton#primary:hover { background: #17644f; }
            QPushButton:disabled { background: #c5cfcb; color: #f4f6f5; }
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
                and (
                    self.show_all_windows_check.isChecked()
                    or "visual studio code" in title.casefold()
                )
            ]
        except Exception as error:
            self.log.appendPlainText(f"读取窗口列表失败：{error}")
            return
        for hwnd, title in windows:
            self.window_combo.addItem(title, hwnd)
        index = self.window_combo.findData(current_hwnd)
        if index < 0:
            index = self.window_combo.findText(current, Qt.MatchFlag.MatchExactly)
        if index < 0 and not self.show_all_windows_check.isChecked():
            index = self.window_combo.findText(
                "Visual Studio Code", Qt.MatchFlag.MatchContains
            )
        if index >= 0:
            self.window_combo.setCurrentIndex(index)
        elif not windows and not self.show_all_windows_check.isChecked():
            self.window_combo.addItem("未找到 VS Code（启动后仍可监测）", 0)
        if not windows:
            self.status_label.setText("没有找到目标窗口")

    def register_hotkeys(self):
        if sys.platform != "win32" or not self.winId():
            return
        try:
            desired = {
                HOTKEY_SWITCH: parse_global_hotkey(
                    self.switch_hotkey_edit.keySequence()
                ),
                HOTKEY_GLASS: parse_global_hotkey(
                    self.glass_hotkey_edit.keySequence()
                ),
            }
            if desired[HOTKEY_SWITCH][1] == desired[HOTKEY_GLASS][1] and (
                desired[HOTKEY_SWITCH][0] & 0x3F
            ) == (desired[HOTKEY_GLASS][0] & 0x3F):
                raise ValueError("两个功能不能使用相同快捷键")
        except ValueError as error:
            self.hotkey_status_label.setText(str(error))
            return

        user32 = ctypes.windll.user32
        old = dict(self.registered_hotkeys)
        for hotkey_id in old:
            user32.UnregisterHotKey(int(self.winId()), hotkey_id)
        registered = []
        for hotkey_id, (modifiers, key) in desired.items():
            if not user32.RegisterHotKey(
                int(self.winId()), hotkey_id, modifiers, key
            ):
                for registered_id in registered:
                    user32.UnregisterHotKey(int(self.winId()), registered_id)
                for old_id, (old_modifiers, old_key) in old.items():
                    user32.RegisterHotKey(
                        int(self.winId()), old_id, old_modifiers, old_key
                    )
                self.registered_hotkeys = old
                if HOTKEY_SWITCH in old:
                    self.switch_hotkey_edit.setKeySequence(
                        QKeySequence(
                            self.settings.value("switchHotkey", "Scroll Lock")
                        )
                    )
                if HOTKEY_GLASS in old:
                    self.glass_hotkey_edit.setKeySequence(
                        QKeySequence(self.settings.value("glassHotkey", "Pause"))
                    )
                self.hotkey_status_label.setText(
                    "注册失败：快捷键已被其他程序占用，已保留原绑定"
                )
                return
            registered.append(hotkey_id)
        self.registered_hotkeys = desired
        self.settings.setValue(
            "switchHotkey",
            self.switch_hotkey_edit.keySequence().toString(
                QKeySequence.SequenceFormat.PortableText
            ),
        )
        self.settings.setValue(
            "glassHotkey",
            self.glass_hotkey_edit.keySequence().toString(
                QKeySequence.SequenceFormat.PortableText
            ),
        )
        self.hotkey_status_label.setText("全局快捷键已注册")

    def nativeEvent(self, event_type, message):
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            native_message = ctypes.cast(
                int(message), ctypes.POINTER(ctypes.wintypes.MSG)
            ).contents
            if native_message.message == WM_HOTKEY:
                self.handle_hotkey(int(native_message.wParam))
                return True, 0
        return super().nativeEvent(event_type, message)

    def handle_hotkey(self, hotkey_id):
        if hotkey_id == HOTKEY_SWITCH:
            self.switch_enabled = not self.switch_enabled
            state = "恢复" if self.switch_enabled else "暂停"
            self.append_log(f"多人触发窗口切换：{state}")
            self.status_label.setText(f"窗口切换已{state}")
        elif hotkey_id == HOTKEY_GLASS:
            self.glass_enabled = not self.glass_enabled
            if self.protection_absent:
                self.show_glass_for("none")
            elif self.glass_enabled and self.glass_check.isChecked():
                self.show_glass_for(self.current_screen_mode)
            else:
                self.hide_all_glass()
            state = "恢复" if self.glass_enabled else "关闭"
            self.append_log(f"隐私遮罩：{state}")
            self.status_label.setText(f"隐私遮罩已{state}")

    def hide_all_glass(self):
        for overlay in self.glass_overlays.values():
            overlay.hide()

    def on_screen_configuration_changed(self, *_args):
        if self.protection_absent:
            self.show_glass_for("none")
        else:
            self.show_glass_for(self.current_screen_mode)

    def refresh_overlay_geometry(self, *_args):
        if self.protection_absent:
            self.show_glass_for("none")

    def test_switch(self):
        title = self.window_combo.currentText()
        if not title or not self.window_combo.currentData():
            QMessageBox.warning(self, "没有目标窗口", "请先刷新并选择一个已打开的应用窗口。")
            return
        activated = activate_window(title, hwnd=self.window_combo.currentData())
        if activated:
            self.log.appendPlainText(f"手动测试成功：已切换到 {activated}")
            self.status_label.setText("测试切换成功")
        else:
            self.log.appendPlainText(f"手动测试失败：Windows 未允许激活 {title}")
            self.status_label.setText("测试切换失败")

    def target_window(self):
        hwnd = self.window_combo.currentData()
        if hwnd:
            return self.window_combo.currentText(), hwnd
        if not self.show_all_windows_check.isChecked():
            return "Visual Studio Code", None
        return self.window_combo.currentText(), None

    def toggle_monitoring(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
            return
        if not PYTHONW.is_file() or not DETECTOR.is_file():
            QMessageBox.critical(self, "项目环境不完整", f"找不到运行文件：\n{PYTHONW}")
            return
        title, hwnd = self.target_window()
        if not hwnd and self.show_all_windows_check.isChecked():
            QMessageBox.warning(self, "没有目标窗口", "请先选择一个已打开的应用窗口。")
            return
        if not hwnd:
            title = "Visual Studio Code"
        args = [
            "-u",
            str(DETECTOR),
            "--camera",
            str(self.camera_spin.value()),
            "--ide-title",
            title,
            "--ide-hwnd",
            str(hwnd or 0),
            "--confidence",
            str(self.confidence_spin.value()),
            "--trigger-frames",
            str(self.trigger_frames_spin.value()),
            "--minimum-people",
            str(self.minimum_people_spin.value()),
            "--cooldown",
            str(self.cooldown_spin.value()),
            "--no-activate",
            "--face-profile",
            str(DEFAULT_PROFILE_PATH),
            "--face-match-threshold",
            str(self.face_threshold_spin.value()),
            "--absence-timeout",
            str(self.absence_timeout_spin.value()),
            "--return-timeout",
            str(self.return_timeout_spin.value()),
            "--face-check-every",
            str(self.face_check_every_spin.value()),
            "--no-preview",
        ]
        if self.look_trigger_check.isChecked():
            args.append("--look-trigger")
        if (
            self.head_direction_check.isChecked()
            or self.glass_check.isChecked()
            or self.absence_check.isChecked()
        ):
            args.extend(
                [
                    "--head-direction",
                    "--head-process-every",
                    str(self.head_interval_spin.value()),
                    "--head-left-threshold",
                    str(self.head_left_spin.value()),
                    "--head-right-threshold",
                    str(self.head_right_spin.value()),
                    "--head-stability-frames",
                    str(self.head_stability_spin.value()),
                ]
            )
            if self.head_invert_check.isChecked():
                args.append("--head-yaw-invert")
            args.extend(["--debug-web", "--debug-web-port", "8765"])
        if self.absence_check.isChecked() and DEFAULT_PROFILE_PATH.is_file():
            args.append("--absence-protection")
        elif self.absence_check.isChecked():
            self.append_log("离席保护未启动：请先登记本人并准备 YuNet/SFace 模型")
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
            if line.startswith("{"):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = None
                if event:
                    self.handle_detector_event(event)
                    continue
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
            elif line.startswith("Screen mode:"):
                mode = line.split()[2] if len(line.split()) >= 3 else "none"
                self.handle_screen_mode(mode)
            elif line.startswith("Head direction:"):
                direction = line.split()[2] if len(line.split()) >= 3 else "unknown"
                self.status_label.setText(f"头部方向：{direction}")

    def handle_detector_event(self, event):
        if event.get("event") == "capture":
            path = Path(event.get("path", ""))
            if path.is_file():
                self.latest_capture = path
                self.refresh_captures(path)
        elif event.get("event") == "trigger":
            self.show_border_flash()
            if self.switch_enabled or self.protection_absent:
                title, hwnd = self.target_window()
                activated = activate_window(
                    title,
                    hwnd=hwnd,
                )
                if activated:
                    self.status_label.setText("检测到多人，已切换到目标应用")
                    self.append_log(f"GUI 已切换到 {activated}")
                else:
                    self.status_label.setText("检测到多人，但目标窗口不可用")
            else:
                self.status_label.setText("检测到多人，切换已暂停")
        elif event.get("event") == "screen":
            mode = event.get("mode", "none")
            if self.protection_absent:
                self.protection_return_mode = mode
            self.handle_screen_mode(mode)
        elif event.get("event") == "protection":
            absent = event.get("state") == "absent"
            self.protection_absent = absent
            if absent:
                self.protection_return_mode = self.current_screen_mode
                self.show_glass_for("none")
                title, hwnd = self.target_window()
                activate_window(title, hwnd=hwnd)
                self.status_label.setText("本人离席：已切换并遮住屏幕")
            else:
                self.handle_screen_mode(self.protection_return_mode)
                self.status_label.setText("本人已返回")
        elif event.get("event") == "face_status":
            score = event.get("score")
            score_text = "无" if score is None else f"{score:.3f}"
            self.face_status_label.setText(
                f"{event.get('state', 'unknown')}，相似度 {score_text}"
            )

    def handle_screen_mode(self, mode):
        self.current_screen_mode = mode
        self.status_label.setText(f"屏幕模式：{mode}")
        if (self.glass_enabled and self.glass_check.isChecked()) or self.protection_absent:
            self.show_glass_for("none" if self.protection_absent else mode)
        else:
            self.hide_all_glass()

    def read_error(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            self.append_log(line)

    def process_finished(self, _exit_code, _exit_status):
        self.start_button.setText("开始监测")
        self.test_button.setEnabled(True)
        self.protection_absent = False
        self.hide_all_glass()
        if self.status_label.text() not in ("已切换到目标应用",):
            self.status_label.setText("监测已停止")
        self.append_log("监测进程已停止")

    def append_log(self, text):
        self.log.appendPlainText(text)

    def enroll_face(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "请先停止监测", "登记时需要独占摄像头。")
            return
        try:
            guard = FaceGuard()
            dialog = EnrollmentDialog(self.camera_spin.value(), guard, self)
        except (FileNotFoundError, RuntimeError, cv2.error, OSError) as error:
            QMessageBox.critical(self, "无法开始登记", str(error))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            accepted = guard.enroll(dialog.frames)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "登记失败", str(error))
            return
        self.face_status_label.setText(f"已登记，采纳 {accepted}/6 张")
        self.append_log(f"已保存本地人脸特征：{DEFAULT_PROFILE_PATH}")
        self.absence_check.setEnabled(True)

    def clear_face_profile(self):
        try:
            DEFAULT_PROFILE_PATH.unlink(missing_ok=True)
        except OSError as error:
            QMessageBox.critical(self, "删除失败", str(error))
            return
        self.face_status_label.setText("尚未登记")
        self.absence_check.setChecked(False)
        self.append_log("已删除本地人脸档案")

    def open_debug_page(self):
        QDesktopServices.openUrl(QUrl(DEBUG_URL))
        self.append_log(f"已打开头向调试页：{DEBUG_URL}")

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

    def ordered_screens(self):
        return sorted(
            QApplication.screens(),
            key=lambda screen: (screen.geometry().x(), screen.geometry().y()),
        )

    def show_glass_for(self, mode):
        self.current_screen_mode = mode
        if not self.glass_enabled and not self.protection_absent:
            self.hide_all_glass()
            return
        if self.protection_absent:
            mode = "none"
        screens = self.ordered_screens()
        if not screens:
            for overlay in self.glass_overlays.values():
                overlay.hide()
            return
        if mode == "center":
            mode = "both"
        if len(screens) == 1:
            overlay = self.glass_overlays.get(0)
            if overlay is None:
                overlay = GlassOverlay(self.glass_opacity_spin.value())
                self.glass_overlays[0] = overlay
            overlay.opacity = self.glass_opacity_spin.value()
            overlay.setGeometry(screens[0].geometry())
            if mode == "both":
                overlay.hide()
            else:
                overlay.show()
                overlay.raise_()
            for index, stale_overlay in self.glass_overlays.items():
                if index != 0:
                    stale_overlay.hide()
            return
        for index, screen in enumerate(screens[:2]):
            overlay = self.glass_overlays.get(index)
            if overlay is None:
                overlay = GlassOverlay(self.glass_opacity_spin.value())
                self.glass_overlays[index] = overlay
            overlay.opacity = self.glass_opacity_spin.value()
            overlay.setGeometry(screen.geometry())
            should_cover = mode == "none" or (
                mode == "left" and index == 1
            ) or (
                mode == "right" and index == 0
            )
            if mode == "both":
                should_cover = False
            if should_cover:
                overlay.show()
                overlay.raise_()
            else:
                overlay.hide()

    def closeEvent(self, event):
        if sys.platform == "win32":
            for hotkey_id in self.registered_hotkeys:
                ctypes.windll.user32.UnregisterHotKey(int(self.winId()), hotkey_id)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
        self.hide_all_glass()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
