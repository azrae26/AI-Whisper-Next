from __future__ import annotations

import ctypes

from PySide6.QtCore import QEvent, QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..models import AppConfig, SUPPORTED_MODELS, TextCorrection
from ..paths import asset_dir
from ..text_processing import corrections_to_text, parse_text_corrections
from .waveform_overlay import WaveformOverlay

HISTORY_MIC_OFFSET_Y = 12
STATUS_FONT_SIZE = 14


def _fix_win11_frame(widget) -> None:
    """Remove gray DWM border on Windows 11 for frameless transparent windows."""
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(widget.winId()),
            ctypes.c_int(34),  # DWMWA_BORDER_COLOR
            ctypes.byref(ctypes.c_uint(0xFFFFFFFE)),  # DWMWA_COLOR_NONE
            ctypes.c_int(ctypes.sizeof(ctypes.c_uint)),
        )
    except Exception:
        pass


def _style() -> str:
    return """
    QWidget { background: #121212; color: #F4F4F5; font-family: "Microsoft JhengHei UI"; }
    QMainWindow, QStackedWidget { background: transparent; }
    QFrame#top { background: transparent; }
    QPushButton { border-radius: 8px; background: #27272A; border: 1px solid #3F3F46; padding: 6px 10px; color:#F4F4F5; outline: none; }
    QPushButton:focus { outline: none; }
    QPushButton:hover { background: #3F3F46; }
    QPushButton#mic { border-radius: 28px; min-height: 56px; max-height: 56px; padding: 0; font-size: 18px; font-weight: 700; }
    QPushButton#ghost { background: transparent; border: 0; color:#A1A1AA; font-size:16px; font-weight:700; }
    QPushButton#ghost:hover { background:#27272A; }
    QPushButton#settingsMenu { background: transparent; border: 0; border-radius: 8px; color:#A1A1AA; font-size:12px; font-weight:700; padding:0; }
    QPushButton#settingsMenu:hover { background:#27272A; color:#F4F4F5; }
    QPushButton#backNav { background: transparent; border: 0; border-radius: 8px; color:#A1A1AA; font-size:22px; font-weight:700; padding:0 0 3px 0; }
    QPushButton#backNav:hover { background:#27272A; color:#F4F4F5; }
    QPushButton#windowControl, QPushButton#windowClose { background: transparent; border: 0; border-radius: 8px; color:#A1A1AA; font-size:18px; font-weight:700; padding:0; }
    QPushButton#windowControl:hover { background:#3F3F46; color:#FFFFFF; }
    QPushButton#windowClose:hover { background:#7F1D1D; color:#FEE2E2; }
    QLineEdit, QTextEdit, QComboBox { background: #27272A; border: 1px solid #3F3F46; border-radius: 8px; padding: 8px; }
    QComboBox { padding-right: 36px; }
    QComboBox::drop-down { width: 34px; border: 0; background: transparent; }
    QComboBox::down-arrow { width: 12px; height: 12px; margin-right: 8px; }
    QPushButton#eyeToggle { background:#242428; border:1px solid #3F3F46; border-radius:8px; padding:0; }
    QPushButton#eyeToggle:hover { background:#2F3036; border-color:#52525B; }
    QScrollArea { border: 0; background: transparent; }
    QScrollArea > QWidget > QWidget { background: transparent; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 6px 2px 6px 2px; border: 0; }
    QScrollBar::handle:vertical { background: #3F3F46; border-radius: 4px; min-height: 32px; }
    QScrollBar::handle:vertical:hover { background: #52525B; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: 0; background: transparent; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px 6px 2px 6px; border: 0; }
    QScrollBar::handle:horizontal { background: #3F3F46; border-radius: 4px; min-width: 32px; }
    QScrollBar::handle:horizontal:hover { background: #52525B; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: 0; background: transparent; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    QCheckBox::indicator { width: 36px; height: 20px; }
    """


def _create_window_button(text: str, close: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("windowClose" if close else "windowControl")
    btn.setFixedSize(30, 30)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _status_style(color: str) -> str:
    return f"color:{color};font-size:{STATUS_FONT_SIZE}px;font-weight:700;"


class ToggleSwitch(QCheckBox):
    def __init__(self):
        super().__init__("")
        self.setFixedSize(46, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(46, 24)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(1, 2, -1, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2563EB") if self.isChecked() else QColor("#27272A"))
        painter.drawRoundedRect(track, 10, 10)
        if not self.isChecked():
            painter.setPen(QColor("#3F3F46"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, 10, 10)
            painter.setPen(Qt.PenStyle.NoPen)
        knob_x = track.right() - 18 if self.isChecked() else track.left() + 2
        painter.setBrush(QColor("#FFFFFF") if self.isChecked() else QColor("#A1A1AA"))
        painter.drawEllipse(knob_x, track.top() + 2, 16, 16)


class EyeButton(QPushButton):
    def __init__(self):
        super().__init__("")
        self._visible = False
        self.setObjectName("eyeToggle")
        self.setFixedSize(42, 42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_visible_state(self, visible: bool) -> None:
        self._visible = visible
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        color = QColor("#F4F4F5" if self._visible else "#A1A1AA")
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        eye = QPainterPath()
        eye.moveTo(cx - 8, cy)
        eye.cubicTo(cx - 5, cy - 5, cx + 5, cy - 5, cx + 8, cy)
        eye.cubicTo(cx + 5, cy + 5, cx - 5, cy + 5, cx - 8, cy)
        painter.drawPath(eye)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cx - 2.1, cy - 2.1, 4.2, 4.2))
        if not self._visible:
            painter.setPen(QPen(QColor("#8B8B93"), 1.5))
            painter.drawLine(int(cx + 7), int(cy - 7), int(cx - 7), int(cy + 7))


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class SettingsPage(QWidget):
    changed = Signal(object)
    capture_requested = Signal(str, object)
    minimize_requested = Signal()
    close_requested = Signal()

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self._cfg = cfg
        self._loading = False
        self._capture_buttons: dict[str, QPushButton] = {}
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        top = QFrame()
        top.setObjectName("top")
        self.title_bar = top
        top.setFixedHeight(56)
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(12, 10, 12, 10)
        self.back_btn = QPushButton("‹")
        self.back_btn.setObjectName("backNav")
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        top_l.addWidget(self.back_btn)
        title = QLabel("設定")
        self.title_label = title
        title.setStyleSheet("font-size:18px;font-weight:700;")
        top_l.addWidget(title, 1)
        self.window_controls = QWidget()
        self.window_controls.setStyleSheet("background:transparent;")
        controls_l = QHBoxLayout(self.window_controls)
        controls_l.setContentsMargins(0, 0, 0, 0)
        controls_l.setSpacing(2)
        self.minimize_btn = _create_window_button("–")
        controls_l.addWidget(self.minimize_btn)
        self.close_btn = _create_window_button("×", close=True)
        controls_l.addWidget(self.close_btn)
        top_l.addWidget(self.window_controls)
        outer.addWidget(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.form = QVBoxLayout(content)
        self.form.setContentsMargins(20, 16, 12, 16)
        self.form.setSpacing(6)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.startup = ToggleSwitch()
        self._section("開機時自動啟動")
        self.form.addWidget(self.startup)

        self._section("自動分段設定")
        self._hint("靜音超過「送出門檻」秒後自動辨識；累積超過「最長累積」時，只要靜音達「短靜音門檻」即觸發")
        self.segment_silence = self._number_row("送出門檻（秒）")
        self.segment_max_accum = self._number_row("最長累積（秒）")
        self.segment_short_silence = self._number_row("短靜音門檻（秒）")

        self._section("麥克風預熱保持時間")
        self._hint("停止錄音後，麥克風保持預熱狀態的時間（分鐘）；逾時自動關閉，下次使用時重新開啟")
        self.warmup_idle_minutes = self._number_row("保持時間（分鐘）")

        self._section("語音偵測靈敏度")
        self._hint("信心閾值：越低越容易觸發（建議 0.3–0.7）；最短語音：小於此長度的音訊不送辨識（建議 0.1–0.5 秒）")
        self.vad_confidence = self._number_row("信心閾值（0–1）")
        self.vad_min_speech_sec = self._number_row("最短語音（秒）")

        self._section("識別快捷鍵（自動加句號）")
        self._hint("辨識貼上時，游標在文字最後會加句號；點擊按鈕可自訂（Esc 取消）")
        self.hotkey_btn = self._capture_btn("hotkey")

        self._section("識別快捷鍵（自動加逗號）")
        self._hint("辨識貼上時，游標在文字最後會加逗號；點擊按鈕可自訂（Esc 取消）")
        self.hotkey_comma_btn = self._capture_btn("hotkey_comma")

        self._section("歷史識別快捷鍵")
        self._hint("點擊按鈕後，按下想要的組合鍵（Esc 取消）")
        self.history_btns: list[QPushButton] = []
        for i in range(5):
            row = QHBoxLayout()
            label = QLabel(f"記憶 {i + 1}")
            label.setFixedWidth(56)
            label.setStyleSheet("color:#A1A1AA;")
            row.addWidget(label)
            btn = self._capture_btn(f"history_{i}", add=False)
            row.addWidget(btn, 1)
            self.form.addLayout(row)
            self.history_btns.append(btn)

        self._section("OpenAI API Key")
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-...")
        self.api_key.setFixedHeight(42)
        key_row.addWidget(self.api_key, 1)
        self.show_key = EyeButton()
        key_row.addWidget(self.show_key)
        self.form.addLayout(key_row)

        self._section("辨識模型")
        self.model = NoWheelComboBox()
        self.model.addItems(SUPPORTED_MODELS)
        self.form.addWidget(self.model)

        self._section("文字校正")
        self._hint("每行一組，格式：原字,替換字；辨識結果會自動替換")
        self.text_corrections = QTextEdit()
        self.text_corrections.setMinimumHeight(90)
        self.text_corrections.setMaximumHeight(300)
        self.form.addWidget(self.text_corrections)
        self.form.addStretch(1)

        self._wire()
        self.set_config(self._cfg)

    def _section(self, text: str) -> None:
        label = QLabel(text)
        label.setStyleSheet("font-size:14px;font-weight:700;color:#D4D4D8;margin-top:12px;")
        self.form.addWidget(label)

    def _hint(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("font-size:12px;color:#71717A;")
        self.form.addWidget(label)

    def _number_row(self, label_text: str) -> QLineEdit:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setStyleSheet("color:#A1A1AA;")
        row.addWidget(label, 1)
        entry = QLineEdit()
        entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        entry.setFixedWidth(90)
        row.addWidget(entry)
        self.form.addLayout(row)
        return entry

    def _capture_btn(self, field: str, add: bool = True) -> QPushButton:
        btn = QPushButton("")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setMinimumHeight(36)
        btn.clicked.connect(lambda: self.capture_requested.emit(field, btn))
        self._capture_buttons[field] = btn
        if add:
            self.form.addWidget(btn)
        return btn

    def _wire(self) -> None:
        self.startup.stateChanged.connect(lambda *_: self._emit_changed())
        for entry in [self.segment_silence, self.segment_max_accum, self.segment_short_silence, self.warmup_idle_minutes, self.vad_confidence, self.vad_min_speech_sec, self.api_key]:
            entry.editingFinished.connect(self._emit_changed)
        self.model.currentTextChanged.connect(lambda *_: self._emit_changed())
        self.text_corrections.textChanged.connect(self._on_text_changed)
        self.show_key.clicked.connect(self._toggle_key_visibility)
        self.minimize_btn.clicked.connect(self.minimize_requested.emit)
        self.close_btn.clicked.connect(self.close_requested.emit)

    def _toggle_key_visibility(self) -> None:
        show = self.api_key.echoMode() == QLineEdit.EchoMode.Password
        self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
        self.show_key.set_visible_state(show)

    def _on_text_changed(self) -> None:
        line_count = max(4, len(self.text_corrections.toPlainText().strip().splitlines())) if self.text_corrections.toPlainText().strip() else 4
        self.text_corrections.setFixedHeight(min(300, max(90, line_count * 24)))
        self._emit_changed()

    def _safe_float(self, entry: QLineEdit, fallback: float) -> float:
        try:
            value = float(entry.text())
            return value if value > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    def current_config(self) -> AppConfig:
        cfg = self._cfg
        return AppConfig(
            apiKey=self.api_key.text().strip(),
            hotkey=self.hotkey_btn.text().strip().lower(),
            hotkey_comma=self.hotkey_comma_btn.text().strip().lower(),
            history_hotkeys=[b.text().strip().lower() for b in self.history_btns],
            model=self.model.currentText(),
            startup=self.startup.isChecked(),
            geometry=cfg.geometry,
            text_corrections=parse_text_corrections(self.text_corrections.toPlainText()),
            segment_silence=self._safe_float(self.segment_silence, cfg.segment_silence),
            segment_max_accum=self._safe_float(self.segment_max_accum, cfg.segment_max_accum),
            segment_short_silence=self._safe_float(self.segment_short_silence, cfg.segment_short_silence),
            warmup_idle_minutes=self._safe_float(self.warmup_idle_minutes, cfg.warmup_idle_minutes),
            vad_confidence=self._safe_float(self.vad_confidence, cfg.vad_confidence),
            vad_min_speech_sec=self._safe_float(self.vad_min_speech_sec, cfg.vad_min_speech_sec),
        )

    def _emit_changed(self) -> None:
        if self._loading:
            return
        self.changed.emit(self.current_config())

    def set_config(self, cfg: AppConfig) -> None:
        self._loading = True
        self._cfg = cfg
        try:
            self.startup.setChecked(cfg.startup)
            self.segment_silence.setText(str(cfg.segment_silence))
            self.segment_max_accum.setText(str(cfg.segment_max_accum))
            self.segment_short_silence.setText(str(cfg.segment_short_silence))
            self.warmup_idle_minutes.setText(str(cfg.warmup_idle_minutes))
            self.vad_confidence.setText(str(cfg.vad_confidence))
            self.vad_min_speech_sec.setText(str(cfg.vad_min_speech_sec))
            self.hotkey_btn.setText(cfg.hotkey.upper())
            self.hotkey_comma_btn.setText(cfg.hotkey_comma.upper())
            for i, btn in enumerate(self.history_btns):
                btn.setText(cfg.history_hotkeys[i].upper())
            self.api_key.setText(cfg.apiKey)
            self.model.setCurrentText(cfg.model)
            self.text_corrections.setPlainText(corrections_to_text(cfg.text_corrections))
        finally:
            self._loading = False

    def set_captured_hotkey(self, field: str, hotkey: str) -> None:
        btn = self._capture_buttons[field]
        btn.setText(hotkey.upper())
        btn.setStyleSheet("")
        self._emit_changed()

    def set_capture_prompt(self, field: str) -> None:
        btn = self._capture_buttons[field]
        btn.setText("請按下組合鍵…")
        btn.setStyleSheet("background:#1E3A5F;color:#93C5FD;border-color:#2563EB;")

    def reset_capture_button(self, field: str) -> None:
        value = self._cfg.hotkey
        if field == "hotkey_comma":
            value = self._cfg.hotkey_comma
        elif field.startswith("history_"):
            value = self._cfg.history_hotkeys[int(field.split("_")[1])]
        self._capture_buttons[field].setText(value.upper())
        self._capture_buttons[field].setStyleSheet("")


class MainWindow(QMainWindow):
    toggle_clicked = Signal(str)
    settings_changed = Signal(object)
    capture_requested = Signal(str)
    copy_history_requested = Signal(int)
    tray_quit_requested = Signal()
    geometry_changed = Signal()

    _CORNER_RADIUS = 10

    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.setWindowTitle("AI Whisper")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(380, 520)
        self.resize(420, 580)
        self.setStyleSheet(_style())
        self._drag_widgets: list[QWidget] = []
        self._drag_offset = None
        self._history_widgets: list[QFrame] = []
        self._history: list[str] = []
        self._state = "idle"
        self._mic_centered = True
        self._mic_animation: QPropertyAnimation | None = None
        self.waveform_overlay = WaveformOverlay()
        self._build(cfg)
        self._setup_tray()

    def _build(self, cfg: AppConfig) -> None:
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.main_page = QWidget()
        self.main_page.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.main_page.setAutoFillBackground(False)
        self.main_layout = QVBoxLayout(self.main_page)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self._build_main()
        self.settings_page = SettingsPage(cfg)
        self.settings_page.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.settings_page.setAutoFillBackground(False)
        self.settings_page.back_btn.clicked.connect(self.show_main)
        self.settings_page.changed.connect(self.settings_changed)
        self.settings_page.capture_requested.connect(lambda field, _btn: self.capture_requested.emit(field))
        self.settings_page.minimize_requested.connect(self.showMinimized)
        self.settings_page.close_requested.connect(self.hide)
        self.stack.addWidget(self.main_page)
        self.stack.addWidget(self.settings_page)
        self._register_drag_widgets(self.settings_page.title_bar, self.settings_page.title_label)
        self._disable_button_focus()

    def _window_button(self, text: str, close: bool = False) -> QPushButton:
        btn = _create_window_button(text, close)
        if close:
            btn.clicked.connect(self.hide)
        else:
            btn.clicked.connect(self.showMinimized)
        return btn

    def _register_drag_widgets(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.installEventFilter(self)
            self._drag_widgets.append(widget)

    def eventFilter(self, obj, event) -> bool:
        if obj in self._drag_widgets:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    event.accept()
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(obj, event)

    def _disable_button_focus(self) -> None:
        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _build_main(self) -> None:
        top = QFrame()
        top.setObjectName("top")
        top.setFixedHeight(56)
        top_l = QGridLayout(top)
        top_l.setContentsMargins(12, 8, 12, 8)
        title_wrap = QWidget()
        title_wrap.setStyleSheet("background:transparent;")
        title_l = QHBoxLayout(title_wrap)
        title_l.setContentsMargins(0, 0, 0, 0)
        title_l.setSpacing(8)
        logo = QLabel()
        logo.setStyleSheet("background:transparent;")
        pix = QPixmap(str(asset_dir() / "icon_256.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(23, 23, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            title_l.addWidget(logo)
        title = QLabel("AI Whisper")
        title.setStyleSheet("font-size:18px;font-weight:700;background:transparent;")
        title_l.addWidget(title)
        self._register_drag_widgets(top, title_wrap, logo, title)
        self.settings_btn = QPushButton("•••")
        self.settings_btn.setObjectName("settingsMenu")
        self.settings_btn.setFixedSize(30, 30)
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.show_settings)
        controls = QWidget()
        controls.setStyleSheet("background:transparent;")
        controls_l = QHBoxLayout(controls)
        controls_l.setContentsMargins(0, 0, 0, 0)
        controls_l.setSpacing(2)
        controls_l.addWidget(self._window_button("–"))
        controls_l.addWidget(self._window_button("×", close=True))
        top_l.addWidget(self.settings_btn, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_l.addWidget(title_wrap, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        top_l.addWidget(controls, 0, 0, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.main_layout.addWidget(top)

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        self.content = content
        self.mic_container = QWidget(content)
        self.mic_container.setStyleSheet("background:transparent;")
        content_l = QVBoxLayout(self.mic_container)
        content_l.setContentsMargins(0, 0, 0, 0)
        content_l.setSpacing(0)
        content_l.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.mic_btn = QPushButton("開始錄音")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.setFixedSize(200, 56)
        self.mic_btn.clicked.connect(lambda: self.toggle_clicked.emit("。"))
        content_l.addWidget(self.mic_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        content_l.addSpacing(12)
        self.hotkey_label = QLabel("")
        self.hotkey_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hotkey_label.setWordWrap(True)
        self.hotkey_label.setMaximumWidth(360)
        self.hotkey_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.hotkey_label.setStyleSheet("font-size:12px;color:#71717A;background:transparent;")
        content_l.addWidget(self.hotkey_label)
        status_row = QHBoxLayout()
        status_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row.setContentsMargins(0, 4, 0, 0)
        self.status_label = QLabel("")
        self.timer_label = QLabel("")
        status_row.addWidget(self.status_label)
        status_row.addWidget(self.timer_label)
        content_l.addLayout(status_row)
        self.history_area = QScrollArea()
        self.history_area.setWidgetResizable(True)
        self.history_area.setFrameShape(QFrame.Shape.NoFrame)
        self.history_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.history_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_content = QWidget()
        self.history_layout = QVBoxLayout(self.history_content)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.addStretch(1)
        self.history_area.setWidget(self.history_content)
        self.history_area.setParent(content)
        self.history_area.hide()
        self.main_layout.addWidget(content, 1)
        self._layout_main_content()

    def _setup_tray(self) -> None:
        self._tray_idle_icon = QIcon(str(asset_dir() / "tray_icon.ico"))
        if self._tray_idle_icon.isNull():
            self._tray_idle_icon = QIcon(str(asset_dir() / "icon.ico"))
        self._tray_recording_icon = QIcon(str(asset_dir() / "tray_icon_rec.ico"))
        if self._tray_recording_icon.isNull():
            self._tray_recording_icon = self._tray_idle_icon
        self._window_idle_icon = QIcon(str(asset_dir() / "icon.ico"))
        if self._window_idle_icon.isNull():
            self._window_idle_icon = self._tray_idle_icon
        self._window_recording_icon = self._tray_recording_icon
        self._apply_window_icon(self._window_idle_icon)
        self.tray = QSystemTrayIcon(self._tray_idle_icon, self)
        self.tray.setToolTip("AI Whisper")
        menu = QMenu()
        open_action = menu.addAction("開啟視窗")
        open_action.triggered.connect(self.show_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.tray_quit_requested)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_from_tray() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _apply_window_icon(self, icon: QIcon) -> None:
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def set_hotkey_display(self, hotkey: str, hotkey_comma: str) -> None:
        first = hotkey.upper().replace(" ", "\u00A0")
        second = hotkey_comma.upper().replace(" ", "\u00A0")
        one_line = f"快捷鍵：{first}（句號） / {second}（逗號）"
        if self.hotkey_label.fontMetrics().horizontalAdvance(one_line) <= self.hotkey_label.maximumWidth():
            self.hotkey_label.setText(one_line)
        else:
            self.hotkey_label.setText(f"快捷鍵：{first}（句號）\n{second}（逗號）")

    def show_settings(self) -> None:
        self.stack.setCurrentWidget(self.settings_page)

    def show_main(self) -> None:
        self.stack.setCurrentWidget(self.main_page)

    def show_from_tray(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_recording_state(self) -> None:
        self._state = "recording"
        self.mic_btn.setText("停止錄音")
        self.mic_btn.setEnabled(True)
        self.mic_btn.setStyleSheet("background:#520000;color:#FECACA;border:2px solid #EF4444;border-radius:28px;font-size:18px;font-weight:700;")
        self.status_label.setText("● 錄音中：")
        self.status_label.setStyleSheet(_status_style("#EF4444"))
        self.tray.setIcon(self._tray_recording_icon)
        self._apply_window_icon(self._window_recording_icon)
        self.waveform_overlay.show_recording()

    def set_processing_state(self) -> None:
        self._state = "processing"
        self.mic_btn.setText("處理中…")
        self.mic_btn.setEnabled(False)
        self.mic_btn.setStyleSheet("background:#1E1E24;color:#A1A1AA;border:2px solid #4B5563;border-radius:28px;font-size:18px;font-weight:700;")
        self.status_label.setText("辨識中…")
        self.status_label.setStyleSheet(_status_style("#A78BFA"))
        self.timer_label.setText("")
        self.waveform_overlay.show_processing()

    def set_idle_state(self) -> None:
        self._state = "idle"
        self.mic_btn.setText("開始錄音")
        self.mic_btn.setEnabled(True)
        self.mic_btn.setStyleSheet("background:#27272A;color:#F4F4F5;border:2px solid #3F3F46;border-radius:28px;font-size:18px;font-weight:700;")
        self.status_label.setText("")
        self.timer_label.setText("")
        self.tray.setIcon(self._tray_idle_icon)
        self._apply_window_icon(self._window_idle_icon)
        self.waveform_overlay.hide_overlay()

    def set_status(self, text: str, color: str = "#A1A1AA") -> None:
        self.status_label.setText("" if text == "等待中" else text)
        self.status_label.setStyleSheet(_status_style(color))
        self.timer_label.setText("")

    def set_timer(self, text: str, color: str) -> None:
        self.status_label.setText("● 錄音中：")
        self.status_label.setStyleSheet(_status_style(color))
        self.timer_label.setText(text)
        self.timer_label.setStyleSheet(_status_style(color))
        if self._state == "recording":
            self.mic_btn.setStyleSheet(f"background:#520000;color:#FECACA;border:2px solid {color};border-radius:28px;font-size:18px;font-weight:700;")

    def set_waveform(self, levels: list[float]) -> None:
        self.waveform_overlay.set_levels(levels)

    def show_overlay_status(self, text: str, color: str, duration_ms: int) -> None:
        self.waveform_overlay.show_status(text, color, duration_ms)

    def add_history(self, text: str) -> None:
        if not text:
            return
        self._history.insert(0, text)
        self._history = self._history[:10]
        if self._mic_centered:
            self._animate_mic_up()
            self.history_area.show()
            self._layout_main_content()
        self._render_history()

    def _render_history(self) -> None:
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for i, text in enumerate(self._history):
            card = QFrame()
            card.setStyleSheet("background:#27272A;border-radius:12px;")
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(8)
            badge = QLabel(str(i + 1))
            badge.setFixedSize(20, 20)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background:#3F3F46;border-radius:10px;color:#F4F4F5;"
                "font-family:\"Segoe UI Semibold\";font-size:12px;font-weight:700;"
            )
            row.addWidget(badge)
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet("color:#F4F4F5;font-size:14px;")
            row.addWidget(label, 1)
            btn = QPushButton("複製")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedSize(52, 28)
            btn.setStyleSheet("background:#3F3F46;border:0;border-radius:6px;font-size:13px;color:#F4F4F5;")
            btn.clicked.connect(lambda _=False, idx=i, b=btn: self._copy_clicked(idx, b))
            row.addWidget(btn)
            self.history_layout.addWidget(card)
        self.history_layout.addStretch(1)

    def _copy_clicked(self, idx: int, btn: QPushButton) -> None:
        QApplication.clipboard().setText(self._history[idx])
        btn.setText("✓")
        QTimer.singleShot(1200, lambda: btn.setText("複製"))

    def history_text(self, idx: int) -> str | None:
        if idx < len(self._history):
            return self._history[idx]
        return None

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#121212"))
        painter.drawRoundedRect(QRectF(self.rect()), self._CORNER_RADIUS, self._CORNER_RADIUS)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._layout_main_content)
        self.geometry_changed.emit()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.geometry_changed.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _fix_win11_frame(self)
        QTimer.singleShot(0, self._layout_main_content)

    def _layout_main_content(self) -> None:
        if not hasattr(self, "content"):
            return
        cw = max(0, self.content.width())
        ch = max(0, self.content.height())
        mic_w, mic_h = min(360, max(220, cw - 40)), 120
        rely = 0.07 if not self._mic_centered else 0.35
        x = int((cw - mic_w) / 2)
        y = int(ch * rely) - (HISTORY_MIC_OFFSET_Y if not self._mic_centered else 0)
        self.mic_container.setGeometry(x, y, mic_w, mic_h)
        if self.history_area.isVisible():
            self.history_area.setGeometry(int(cw * 0.05), int(ch * 0.33), int(cw * 0.93), int(ch * 0.67))

    def _animate_mic_up(self) -> None:
        self._mic_centered = False
        cw = max(0, self.content.width())
        ch = max(0, self.content.height())
        mic_w, mic_h = min(360, max(220, cw - 40)), 120
        target = QRect(int((cw - mic_w) / 2), int(ch * 0.07) - HISTORY_MIC_OFFSET_Y, mic_w, mic_h)
        self._mic_animation = QPropertyAnimation(self.mic_container, b"geometry", self)
        self._mic_animation.setDuration(176)
        self._mic_animation.setStartValue(self.mic_container.geometry())
        self._mic_animation.setEndValue(target)
        self._mic_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._mic_animation.start()
