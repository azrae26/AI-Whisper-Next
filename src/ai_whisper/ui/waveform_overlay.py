from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

BAR_COUNT = 35
BAR_WIDTH = 4
BAR_GAP = 2
PAD_X = 16
PAD_Y = 12
CANVAS_H = 48
WIN_W = BAR_COUNT * (BAR_WIDTH + BAR_GAP) - BAR_GAP + PAD_X * 2
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
        self.setFixedSize(WIN_W, WIN_H)
        self._levels: list[float] = []
        self._processing = False
        self._proc_start = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_processing)
        self.hide()

    def show_recording(self) -> None:
        self._processing = False
        self._timer.stop()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self.update()

    def show_processing(self) -> None:
        self._processing = True
        self._proc_start = time.time()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(33)

    def hide_overlay(self) -> None:
        self._processing = False
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
        if not self._processing:
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

        if self._processing:
            elapsed = time.time() - self._proc_start
            t = (math.sin(elapsed * 2 * math.pi) + 1) / 2
            r = int(34 + (103 - 34) * t)
            g = int(211 + (232 - 211) * t)
            b = int(238 + (249 - 238) * t)
            painter.setPen(QColor(r, g, b, 240))
            font = QFont("Microsoft JhengHei UI", 18)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "識別中…")
            return

        data = self._levels
        if len(data) < BAR_COUNT:
            data = [0.0] * (BAR_COUNT - len(data)) + data
        mid = WIN_H / 2
        max_half = CANVAS_H / 2 - 4
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for i, lv in enumerate(data):
            x0 = PAD_X + i * (BAR_WIDTH + BAR_GAP)
            h = max(2, int(lv * max_half))
            color = QColor(103, 232, 249, 240) if lv > 0.6 else QColor(34, 211, 238, 230)
            painter.setBrush(color)
            painter.drawRect(x0, int(mid - h), BAR_WIDTH, int(h * 2))
