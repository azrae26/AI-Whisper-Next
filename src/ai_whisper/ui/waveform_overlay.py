from __future__ import annotations

import ctypes
import math
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

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


BAR_COUNT = 35
BAR_WIDTH = 4
BAR_GAP = 2
PAD_X = 16
PAD_Y = 12
CANVAS_H = 48
BG_EXTRA = 5  # 黑色底色左右各額外延伸的像素（淡出尺寸不變）
WIN_W = BAR_COUNT * (BAR_WIDTH + BAR_GAP) - BAR_GAP + PAD_X * 2 + BG_EXTRA * 2
WIN_H = CANVAS_H + PAD_Y * 2
MARGIN_BOTTOM = 80


class WaveformOverlay(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(WIN_W, WIN_H)
        self._levels: list[float] = []
        self._processing = False
        self._proc_start = 0.0
        self._status_text = ""
        self._status_color = QColor(16, 185, 129)
        self._status_until = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_processing)
        # Prime the window surface so it renders correctly on first use
        # without needing the app to have been in the foreground first
        self.show()
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _fix_win11_frame(self)

    def show_recording(self) -> None:
        self._processing = False
        self._status_text = ""
        self._levels = []
        self._timer.stop()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self.update()

    def show_processing(self) -> None:
        self._processing = True
        self._status_text = ""
        self._proc_start = time.time()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(33)

    def show_status(self, text: str, color: str, duration_ms: int) -> None:
        self._processing = False
        self._status_text = text
        self._status_color = QColor(color)
        self._status_until = time.time() + duration_ms / 1000
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(33)

    def hide_overlay(self) -> None:
        self._processing = False
        self._status_text = ""
        self._timer.stop()
        self.hide()

    def set_levels(self, levels: list[float]) -> None:
        if self._processing:
            return
        self._levels = levels[-BAR_COUNT:]
        self.update()

    def _position_at_cursor_screen(self) -> None:
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QApplication.primaryScreen()
        rect = screen.availableGeometry()
        x = rect.left() + (rect.width() - WIN_W) // 2
        y = rect.bottom() - WIN_H - MARGIN_BOTTOM
        self.move(x, y)

    def _tick_processing(self) -> None:
        if self._status_text and time.time() >= self._status_until:
            self.hide_overlay()
            return
        if not self._processing and not self._status_text:
            return
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, WIN_W, WIN_H)
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.setClipPath(path)
        mid = WIN_H / 2
        for y in range(WIN_H):
            t = y / mid if y < mid else (WIN_H - 1 - y) / mid
            t = max(0.0, min(1.0, t))
            t = t * t * (3 - 2 * t)
            painter.fillRect(0, y, WIN_W, 1, QColor(15, 15, 35, int(t * 153)))
        painter.setClipping(False)

        # 背景左右淡出（三種狀態共用，畫在所有內容之前）
        bg_fade_w = PAD_X + BG_EXTRA + (BAR_WIDTH + BAR_GAP) * 2 + 5
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        lg_l = QLinearGradient(0, 0, bg_fade_w, 0)
        lg_l.setColorAt(0.0, QColor(0, 0, 0, 255))
        lg_l.setColorAt(0.5, QColor(0, 0, 0, 68))
        lg_l.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0, 0, bg_fade_w, WIN_H), lg_l)
        lg_r = QLinearGradient(WIN_W, 0, WIN_W - bg_fade_w, 0)
        lg_r.setColorAt(0.0, QColor(0, 0, 0, 255))
        lg_r.setColorAt(0.5, QColor(0, 0, 0, 68))
        lg_r.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(WIN_W - bg_fade_w, 0, bg_fade_w, WIN_H), lg_r)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if self._processing:
            elapsed = time.time() - self._proc_start
            t = (math.sin(elapsed * 2 * math.pi) + 1) / 2
            r = int(34 + (165 - 34) * t)
            g = int(211 + (243 - 211) * t)
            b = int(238 + (252 - 238) * t)
            painter.setPen(QColor(r, g, b))
            font = QFont("Microsoft JhengHei UI", 13)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "識別中…")
            return

        if self._status_text:
            painter.setPen(self._status_color)
            font = QFont("Microsoft JhengHei UI", 13)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._status_text)
            return

        # 第二組：bar 本身的左右淡出（per-bar alpha）
        data = self._levels
        if len(data) < BAR_COUNT:
            data = [0.0] * (BAR_COUNT - len(data)) + data
        mid = WIN_H / 2
        max_half = CANVAS_H / 2 - 4
        fade_bars = 4
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for i, lv in enumerate(data):
            dist = min(i, BAR_COUNT - 1 - i)
            edge_t = min(1.0, dist / fade_bars)
            edge_t = 1 - (1 - edge_t) ** 1.6  # ease-out: 先快後慢，邊緣柔和起步
            x0 = PAD_X + BG_EXTRA + i * (BAR_WIDTH + BAR_GAP)
            h = max(2, int(lv * max_half))
            if lv > 0.6:
                color = QColor(103, 232, 249, int(240 * edge_t))
            else:
                color = QColor(34, 211, 238, int(230 * edge_t))
            painter.setBrush(color)
            painter.drawRect(x0, int(mid - h), BAR_WIDTH, int(h * 2))
