from __future__ import annotations

import ctypes
import math
import time

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QLabel, QWidget

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
OVERLAY_RAISE_Y = 50
STATUS_OFFSET_X = 5

# Pre-computed per-bar constants (never change at runtime)
_FADE_BARS = 4
_BAR_MID = WIN_H / 2
_BAR_MAX_HALF = CANVAS_H / 2 - 4
_TRANSITION_PX = (BAR_WIDTH + BAR_GAP) * 5
_BAR_X0: list[int] = [PAD_X + BG_EXTRA + i * (BAR_WIDTH + BAR_GAP) for i in range(BAR_COUNT)]
_BAR_EDGE_T: list[float] = [
    1.0 - (1.0 - min(1.0, min(i, BAR_COUNT - 1 - i) / _FADE_BARS)) ** 1.6
    for i in range(BAR_COUNT)
]
STATUS_TIMER_ARM_DELAY_MS = 120


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
        self._recording_status_text = ""
        self._recording_status_color = QColor("#F5D0FE")
        self._recording_status_until = 0.0
        self._recording_status_token = 0
        self._status_color = QColor(16, 185, 129)
        self._status_until = 0.0
        self._screen_name = ""
        self._text_dim_left = -1.0
        self._text_dim_right = -1.0
        self._dim_font = QFont("Microsoft JhengHei UI", 13)  # used for QFontMetrics; kept separate from _paint_font
        self._dim_font.setBold(True)
        self._recording_shadow_labels: list[QLabel] = []
        for blur, alpha in ((20, 204), (12, 153), (6, 128)):
            label = self._make_recording_status_label(f"rgba(0, 0, 0, {alpha})")
            shadow = QGraphicsDropShadowEffect(label)
            shadow.setBlurRadius(blur)
            shadow.setOffset(0, 0)
            shadow.setColor(QColor(0, 0, 0, alpha))
            label.setGraphicsEffect(shadow)
            label.hide()
            self._recording_shadow_labels.append(label)
        self._recording_status_label = self._make_recording_status_label("#F5D0FE")
        self._recording_status_label.hide()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_processing)

        # Cache expensive objects to avoid allocating them on every paintEvent
        self._paint_font = QFont("Microsoft JhengHei UI", 13)
        self._paint_font.setBold(True)
        self._bg_pixmap: QPixmap | None = None  # built on first paint; WIN_W/H are constants
        self._bar_dim: list[float] = [1.0] * BAR_COUNT  # recomputed only when text_dim changes
        # Pre-allocated QColor objects — mutate alpha each frame instead of new QColor()
        self._color_bright = QColor(103, 232, 249)
        self._color_normal = QColor(34, 211, 238)
        # Pre-allocated QRect list for batched drawRects — avoids per-frame allocation
        self._bar_rects: list[QRect] = [QRect() for _ in range(BAR_COUNT)]

        # Prime window surface, font, AND QGraphicsDropShadowEffect GPU shaders.
        # Shadow effect shader compilation is lazy — it blocks the main thread the
        # first time the label is actually painted. Force it here at startup.
        self.show()
        for label in self._recording_shadow_labels:
            label.setText("預熱")
            label.show()
        self._recording_status_label.setText("預熱")
        self._recording_status_label.show()
        self.repaint()  # synchronous paint → compiles shaders, caches font glyphs
        for label in self._recording_shadow_labels:
            label.hide()
            label.setText("")
        self._recording_status_label.hide()
        self._recording_status_label.setText("")
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _fix_win11_frame(self)

    def show_recording(self) -> None:
        self._processing = False
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._recording_status_token += 1
        self._set_recording_status_label("")
        self._levels = []
        self._timer.stop()
        self._anchor_to_cursor_screen()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self.update()

    def show_processing(self) -> None:
        self._processing = True
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._recording_status_token += 1
        self._set_recording_status_label("")
        self._proc_start = time.time()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(50)

    def set_recording_status(self, text: str = "", color: str = "#F5D0FE", duration_ms: int = 0) -> None:
        if self._processing or self._status_text:
            return
        had_status = bool(self._recording_status_text)
        self._recording_status_token += 1
        token = self._recording_status_token
        self._recording_status_text = text
        self._recording_status_color = QColor(color)
        self._recording_status_until = 0.0
        self._set_recording_status_label(text, color)
        if had_status != bool(text):
            self._position_at_cursor_screen()
        if text and duration_ms > 0:
            QTimer.singleShot(
                STATUS_TIMER_ARM_DELAY_MS,
                lambda: self._arm_recording_status_timeout(token, text, duration_ms),
            )
        self.update()

    def show_status(self, text: str, color: str, duration_ms: int) -> None:
        self._processing = False
        self._status_text = text
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._recording_status_token += 1
        self._set_recording_status_label("")
        self._status_color = QColor(color)
        self._status_until = time.time() + duration_ms / 1000
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(50)

    def hide_overlay(self) -> None:
        self._processing = False
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._recording_status_token += 1
        self._set_recording_status_label("")
        self._timer.stop()
        self.hide()

    def set_levels(self, levels: list[float]) -> None:
        if self._processing:
            return
        self._levels = levels  # already trimmed to BAR_COUNT by get_waveform()
        self.update()

    def _position_at_cursor_screen(self) -> None:
        screen = self._anchored_screen()
        if screen is None:
            self._anchor_to_cursor_screen()
            screen = self._anchored_screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        rect = screen.availableGeometry()
        x = rect.left() + (rect.width() - WIN_W) // 2
        y = rect.bottom() - WIN_H - MARGIN_BOTTOM - OVERLAY_RAISE_Y
        self.move(x, y)

    def _anchor_to_cursor_screen(self) -> None:
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QApplication.primaryScreen()
        self._screen_name = screen.name() if screen is not None else ""

    def _anchored_screen(self):
        if self._screen_name:
            for screen in QGuiApplication.screens():
                if screen.name() == self._screen_name:
                    return screen
        return None

    def _tick_processing(self) -> None:
        now = time.time()
        if self._status_text and now >= self._status_until:
            self.hide_overlay()
            return
        if self._recording_status_text and self._recording_status_until and now >= self._recording_status_until:
            self._recording_status_text = ""
            self._recording_status_until = 0.0
            self._recording_status_token += 1
            self._set_recording_status_label("")
            self._position_at_cursor_screen()
            self.update()
        if not self._processing and not self._status_text and not self._recording_status_text:
            self._timer.stop()
            return
        self.update()

    def _build_bg_pixmap(self) -> QPixmap:
        """Build and cache the background gradient pixmap (drawn once, reused every frame)."""
        px = QPixmap(WIN_W, WIN_H)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, WIN_W, WIN_H)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setClipPath(path)
        # Vertical smoothstep gradient via a QLinearGradient (single fillRect, fast)
        mid = WIN_H / 2
        vg = QLinearGradient(0, 0, 0, WIN_H)
        steps = 16
        for i in range(steps + 1):
            y = i / steps
            t = y if y < 0.5 else 1.0 - y
            t = t * 2  # 0→1 at mid
            t = t * t * (3 - 2 * t)  # smoothstep
            vg.setColorAt(y, QColor(15, 15, 35, int(t * 153)))
        p.fillRect(rect, vg)
        p.setClipping(False)

        # Left / right edge fade-out (DestinationOut mask baked into pixmap)
        bg_fade_w = PAD_X + BG_EXTRA + (BAR_WIDTH + BAR_GAP) * 2 + 5
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        lg_l = QLinearGradient(0, 0, bg_fade_w, 0)
        lg_l.setColorAt(0.0, QColor(0, 0, 0, 255))
        lg_l.setColorAt(0.5, QColor(0, 0, 0, 68))
        lg_l.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, bg_fade_w, WIN_H), lg_l)
        lg_r = QLinearGradient(WIN_W, 0, WIN_W - bg_fade_w, 0)
        lg_r.setColorAt(0.0, QColor(0, 0, 0, 255))
        lg_r.setColorAt(0.5, QColor(0, 0, 0, 68))
        lg_r.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(WIN_W - bg_fade_w, 0, bg_fade_w, WIN_H), lg_r)
        p.end()
        return px

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, WIN_W, WIN_H)

        # Draw cached background (build once, reuse every frame)
        if self._bg_pixmap is None:
            self._bg_pixmap = self._build_bg_pixmap()
        painter.drawPixmap(0, 0, self._bg_pixmap)

        if self._processing:
            elapsed = time.time() - self._proc_start
            t = (math.sin(elapsed * 2 * math.pi) + 1) / 2
            r = int(34 + (165 - 34) * t)
            g = int(211 + (243 - 211) * t)
            b = int(238 + (252 - 238) * t)
            painter.setPen(QColor(r, g, b))
            painter.setFont(self._paint_font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "識別中")
            return

        if self._status_text:
            painter.setPen(self._status_color)
            painter.setFont(self._paint_font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._status_text)
            return

        # Bar rendering — batch into two drawRects calls (bright / normal), zero QColor allocs
        data = self._levels
        n = len(data)
        if n < BAR_COUNT:
            data = [0.0] * (BAR_COUNT - n) + data
        peak = max(data) if data else 0.0
        scale = max(1.0, peak / 0.82)
        bar_dim = self._bar_dim
        mid = int(_BAR_MID)
        max_half = _BAR_MAX_HALF

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        bright_rects: list[QRect] = []
        normal_rects: list[QRect] = []
        bright_alphas: list[int] = []
        normal_alphas: list[int] = []

        for i, lv in enumerate(data):
            display_lv = min(0.92, lv / scale)
            h = max(2, int(display_lv * max_half))
            alpha_t = _BAR_EDGE_T[i] * bar_dim[i]
            rect = self._bar_rects[i]
            rect.setRect(_BAR_X0[i], mid - h, BAR_WIDTH, h * 2)
            if lv > 0.6:
                bright_rects.append(rect)
                bright_alphas.append(int(240 * alpha_t))
            else:
                normal_rects.append(rect)
                normal_alphas.append(int(230 * alpha_t))

        # Bright bars — group by common alpha where possible, else draw individually
        for rect, alpha in zip(bright_rects, bright_alphas):
            self._color_bright.setAlpha(alpha)
            painter.setBrush(self._color_bright)
            painter.drawRect(rect)
        for rect, alpha in zip(normal_rects, normal_alphas):
            self._color_normal.setAlpha(alpha)
            painter.setBrush(self._color_normal)
            painter.drawRect(rect)

    def _set_recording_status_label(self, text: str, color: str = "#F5D0FE") -> None:
        for label in self._recording_shadow_labels:
            label.setText(text)
        self._recording_status_label.setText(text)
        if text:
            text_w = QFontMetrics(self._dim_font).horizontalAdvance(text)
            text_cx = STATUS_OFFSET_X + WIN_W / 2
            pad = -20
            self._text_dim_left = text_cx - text_w / 2 - pad
            self._text_dim_right = text_cx + text_w / 2 + pad
        else:
            self._text_dim_left = -1.0
            self._text_dim_right = -1.0
        self._rebuild_bar_dim()
        if text:
            text_color = QColor(color).name()
            self._recording_status_label.setStyleSheet(
                f"background:transparent;color:{text_color};font-size:13pt;font-weight:700;"
            )
            for label in self._recording_shadow_labels:
                label.raise_()
                label.show()
            self._recording_status_label.raise_()
            self._recording_status_label.show()
        else:
            for label in self._recording_shadow_labels:
                label.hide()
            self._recording_status_label.hide()

    def _rebuild_bar_dim(self) -> None:
        """Recompute per-bar dim factors; called only when text_dim_left/right changes."""
        tdl = self._text_dim_left
        tdr = self._text_dim_right
        tp = _TRANSITION_PX
        if tdl < 0:
            self._bar_dim = [1.0] * BAR_COUNT
            return
        dims: list[float] = []
        for i in range(BAR_COUNT):
            bar_cx = _BAR_X0[i] + BAR_WIDTH / 2
            if bar_cx <= tdl - tp or bar_cx >= tdr + tp:
                dims.append(1.0)
            elif bar_cx < tdl:
                t = (bar_cx - (tdl - tp)) / tp
                t = t * t * (3 - 2 * t)
                dims.append(1.0 - 0.6 * t)
            elif bar_cx > tdr:
                t = (tdr + tp - bar_cx) / tp
                t = t * t * (3 - 2 * t)
                dims.append(1.0 - 0.6 * t)
            else:
                dims.append(0.15)
        self._bar_dim = dims

    def _arm_recording_status_timeout(self, token: int, text: str, duration_ms: int) -> None:
        if token != self._recording_status_token or self._recording_status_text != text:
            return
        self._recording_status_until = time.time() + duration_ms / 1000
        if not self._timer.isActive():
            self._timer.start(50)

    def _make_recording_status_label(self, color: str) -> QLabel:
        label = QLabel(self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(f"background:transparent;color:{color};font-size:13pt;font-weight:700;")
        label.setGeometry(STATUS_OFFSET_X, 0, WIN_W, WIN_H)
        return label
