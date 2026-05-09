from __future__ import annotations

import ctypes
import re
import sys
import threading

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QWidget

from .paths import asset_dir, ensure_runtime_dirs, log_dir


class SplashScreen(QWidget):
    SIZE = 280

    def __init__(self, icon_path):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._icon_pix = QPixmap(str(icon_path))
        self._dot_frame = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(350)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.center().x() - self.SIZE // 2, screen.center().y() - self.SIZE // 2)

    def _tick(self):
        self._dot_frame = (self._dot_frame + 1) % 3
        self.update()

    def finish(self, _target):
        self._timer.stop()
        self.close()

    def paintEvent(self, _event):
        w = h = self.SIZE
        scale = 2
        pw = ph = w * scale

        buf = QPixmap(pw, ph)
        buf.fill(Qt.GlobalColor.transparent)
        p = QPainter(buf)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # background
        bg = QRectF(2, 2, pw - 4, ph - 4)
        grad = QLinearGradient(0, 0, 0, ph)
        grad.setColorAt(0.0, QColor("#1C1C1F"))
        grad.setColorAt(1.0, QColor("#0E0E10"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(bg, 20 * scale, 20 * scale)

        pen = QPen(QColor("#2E2E33"))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bg, 20 * scale, 20 * scale)

        cx = pw // 2

        # layout dimensions (buffer px)
        icon_size = 64 * scale
        gap1 = 12 * scale
        title_h = 28 * scale
        gap2 = (36 - 16) * scale
        dot_r = 4 * scale
        dot_active_r = dot_r + scale
        dot_diameter = dot_active_r * 2

        total_h = icon_size + gap1 + title_h + gap2 + dot_diameter
        icon_y = (ph - total_h) // 2

        # icon + glow
        if not self._icon_pix.isNull():
            glow = QRadialGradient(cx, icon_y + icon_size // 2, icon_size * 0.72)
            glow.setColorAt(0.0, QColor(99, 102, 241, 50))
            glow.setColorAt(1.0, QColor(99, 102, 241, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            r = int(icon_size * 0.76)
            p.drawEllipse(cx - r, icon_y + icon_size // 2 - r, r * 2, r * 2)
            si = self._icon_pix.scaled(icon_size, icon_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(cx - icon_size // 2, icon_y, si)

        # title
        font_title = QFont("Microsoft JhengHei UI", 11 * scale, QFont.Weight.Bold)
        p.setFont(font_title)
        p.setPen(QColor("#F4F4F5"))
        title_y = icon_y + icon_size + gap1
        p.drawText(0, title_y, pw, title_h, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "AI Whisper")

        # animated dots
        dot_gap = 12 * scale
        total_dots_w = 3 * dot_r * 2 + 2 * dot_gap
        dot_x0 = (pw - total_dots_w) // 2
        dot_y = title_y + title_h + gap2
        for i in range(3):
            active = (i == self._dot_frame)
            radius = dot_active_r if active else dot_r
            color = QColor(139, 92, 246, 255) if active else QColor(82, 82, 91, 160)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            dx = dot_x0 + i * (dot_r * 2 + dot_gap)
            offset = dot_active_r - radius
            p.drawEllipse(dx + offset, dot_y + offset, radius * 2, radius * 2)

        p.end()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, buf.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        painter.end()


class CompactPasswordStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_LineEdit_PasswordCharacter:
            return 0x2022
        return super().styleHint(hint, option, widget, returnData)


def _apply_geometry(window, geometry: str) -> None:
    match = re.match(r"(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?", geometry or "")
    if not match:
        return
    w, h = int(match.group(1)), int(match.group(2))
    window.resize(w, h)
    if match.group(3) is not None:
        window.move(int(match.group(3)), int(match.group(4)))


def main() -> int:
    ensure_runtime_dirs()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("AI Whisper")
    app.setStyle(CompactPasswordStyle(app.style()))
    icon_path = asset_dir() / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Show splash immediately, then load heavy modules in background thread
    # so the event loop stays alive and dots can animate
    splash = SplashScreen(asset_dir() / "icon_256.png")
    splash.show()
    app.processEvents()

    _load_result: dict = {}
    _load_done = threading.Event()

    def _background_load():
        from .logging_setup import install_log_tee
        from .services.settings_store import SettingsStore
        from .controller import AppController
        from .ui.main_window import MainWindow
        install_log_tee(log_dir())
        settings = SettingsStore()
        cfg = settings.get()
        _load_result['settings'] = settings
        _load_result['cfg'] = cfg
        _load_result['AppController'] = AppController
        _load_result['MainWindow'] = MainWindow
        _load_done.set()

    threading.Thread(target=_background_load, daemon=True).start()

    # _refs lives in main() scope until app.exec() returns — keeps objects alive
    _refs: dict = {}

    def _check_loaded():
        if _load_done.is_set():
            window = _load_result['MainWindow'](_load_result['cfg'])
            _apply_geometry(window, _load_result['cfg'].geometry)
            controller = _load_result['AppController'](window, _load_result['settings'])
            app.aboutToQuit.connect(controller.cleanup)
            _refs['window'] = window
            _refs['controller'] = controller
            _refs['waveform_overlay'] = window.waveform_overlay
            splash.finish(window)
            window.show()
        else:
            QTimer.singleShot(50, _check_loaded)

    QTimer.singleShot(50, _check_loaded)
    return app.exec()
