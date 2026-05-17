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
STATUS_PURPLE_MID = "#e2b8ff"
WAVEFORM_COLOR_DARK = "#d79bff"
WAVEFORM_COLOR_MID = "#e2b8ff"
WAVEFORM_COLOR_LIGHT = "#f2deff"
STATUS_PROCESSING_LIGHT = "#9af4ff"
STATUS_PROCESSING_DARK = "#00d3f3"


def _mix_three_stop_color(dark: QColor, mid: QColor, light: QColor, t: float, alpha: int) -> QColor:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        left = dark
        right = mid
        local_t = t * 2
    else:
        left = mid
        right = light
        local_t = (t - 0.5) * 2
    r = int(left.red() + (right.red() - left.red()) * local_t)
    g = int(left.green() + (right.green() - left.green()) * local_t)
    b = int(left.blue() + (right.blue() - left.blue()) * local_t)
    return QColor(r, g, b, alpha)


def _waveform_color_position(display_level: float) -> float:
    return max(0.0, min(1.0, display_level)) ** 1.22

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
        self._waveform_visible = False
        self._processing = False
        self._proc_start = 0.0
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_color = QColor(STATUS_PURPLE_MID)
        self._recording_status_until = 0.0
        self._recording_status_token = 0
        self._hide_after_recording_status = False
        self._status_color = QColor(16, 185, 129)
        self._status_until = 0.0
        self._screen_name = ""
        self._text_dim_left = -1.0
        self._text_dim_right = -1.0
        self._dim_font = QFont("Microsoft JhengHei UI", 13)  # used for QFontMetrics; kept separate from _paint_font
        self._dim_font.setBold(True)
        self._status_shadow_labels: list[QLabel] = []
        for blur, alpha in ((20, 204), (12, 153), (6, 128)):
            label = self._make_status_label(f"rgba(0, 0, 0, {alpha})")
            shadow = QGraphicsDropShadowEffect(label)
            shadow.setBlurRadius(blur)
            shadow.setOffset(0, 0)
            shadow.setColor(QColor(0, 0, 0, alpha))
            label.setGraphicsEffect(shadow)
            label.hide()
            self._status_shadow_labels.append(label)
        self._status_label = self._make_status_label(STATUS_PURPLE_MID)
        self._status_label.hide()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_processing)

        # Cache expensive objects to avoid allocating them on every paintEvent
        self._paint_font = QFont("Microsoft JhengHei UI", 13)
        self._paint_font.setBold(True)
        self._bg_pixmap: QPixmap | None = None  # built on first paint; WIN_W/H are constants
        self._bar_dim: list[float] = [1.0] * BAR_COUNT  # recomputed only when text_dim changes
        self._waveform_color_light = QColor(WAVEFORM_COLOR_LIGHT)
        self._waveform_color_mid = QColor(WAVEFORM_COLOR_MID)
        self._waveform_color_dark = QColor(WAVEFORM_COLOR_DARK)
        # Pre-allocated QRect list for batched drawRects — avoids per-frame allocation
        self._bar_rects: list[QRect] = [QRect() for _ in range(BAR_COUNT)]

        # Prime window surface, font, and shadow effect before the first real overlay show.
        self.show()
        self._set_status_label("預熱", QColor(STATUS_PURPLE_MID), on_waveform=True)
        self.repaint()  # synchronous paint -> caches font glyphs
        self._set_status_label("")
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _fix_win11_frame(self)

    def show_recording(self) -> None:
        self._waveform_visible = True
        self._processing = False
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._hide_after_recording_status = False
        self._recording_status_token += 1
        self._update_recording_status_metrics("")
        self._set_status_label("")
        self._levels = []
        self._timer.stop()
        self._anchor_to_cursor_screen()
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self.update()

    def show_processing(self) -> None:
        self._waveform_visible = False
        self._processing = True
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._hide_after_recording_status = False
        self._recording_status_token += 1
        self._update_recording_status_metrics("")
        self._proc_start = time.time()
        self._set_status_label("識別中", self._processing_color(), on_waveform=False)
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(50)

    def set_recording_status(self, text: str = "", color: str = STATUS_PURPLE_MID, duration_ms: int = 0) -> None:
        if self._processing or self._status_text:
            return
        self._hide_after_recording_status = not self._waveform_visible
        had_status = bool(self._recording_status_text)
        self._recording_status_token += 1
        token = self._recording_status_token
        self._recording_status_text = text
        self._recording_status_color = QColor(color)
        self._recording_status_until = 0.0
        self._update_recording_status_metrics(text, color)
        self._set_status_label(text, self._recording_status_color, on_waveform=self._waveform_visible)
        if had_status != bool(text):
            self._position_at_cursor_screen()
        if text and duration_ms > 0:
            QTimer.singleShot(
                STATUS_TIMER_ARM_DELAY_MS,
                lambda: self._arm_recording_status_timeout(token, text, duration_ms),
            )
        self.update()

    def show_status(self, text: str, color: str, duration_ms: int) -> None:
        self._waveform_visible = False
        self._processing = False
        self._status_text = text
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._hide_after_recording_status = False
        self._recording_status_token += 1
        self._update_recording_status_metrics("")
        self._status_color = QColor(color)
        self._set_status_label(text, self._status_color, on_waveform=False)
        self._status_until = time.time() + duration_ms / 1000
        self._position_at_cursor_screen()
        self.show()
        self.raise_()
        self._timer.start(50)

    def hide_overlay(self) -> None:
        self._waveform_visible = False
        self._processing = False
        self._status_text = ""
        self._recording_status_text = ""
        self._recording_status_until = 0.0
        self._hide_after_recording_status = False
        self._recording_status_token += 1
        self._update_recording_status_metrics("")
        self._set_status_label("")
        self._timer.stop()
        self.hide()

    def finish_recording_without_replay(self) -> None:
        self._waveform_visible = False
        self._processing = False
        self._status_text = ""
        self._levels = []
        if self._recording_status_text:
            self._hide_after_recording_status = True
            self._set_status_label(self._recording_status_text, self._recording_status_color, on_waveform=False)
            self._position_at_cursor_screen()
            self.show()
            self.raise_()
            if self._recording_status_until and not self._timer.isActive():
                self._timer.start(50)
            self.update()
        else:
            self.hide_overlay()

    def stop_waveform_keep_status(self) -> None:
        self._waveform_visible = False
        self._levels = []
        if self._recording_status_text:
            self._hide_after_recording_status = True
            self._set_status_label(self._recording_status_text, self._recording_status_color, on_waveform=False)
            if self._recording_status_until and not self._timer.isActive():
                self._timer.start(50)
            self.show()
            self.raise_()
        else:
            self._processing = True
            self._hide_after_recording_status = False
            self._proc_start = time.time()
            self._set_status_label("識別中", self._processing_color(), on_waveform=False)
            if not self._timer.isActive():
                self._timer.start(50)
            self.show()
            self.raise_()
        self.update()

    def set_levels(self, levels: list[float]) -> None:
        if not self._waveform_visible or self._processing:
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
        if self._processing:
            self._set_status_label("識別中", self._processing_color(), on_waveform=False)
        if self._status_text and now >= self._status_until:
            self.hide_overlay()
            return
        if self._recording_status_text and self._recording_status_until and now >= self._recording_status_until:
            self._recording_status_text = ""
            self._recording_status_until = 0.0
            self._recording_status_token += 1
            self._update_recording_status_metrics("")
            self._set_status_label("")
            if self._hide_after_recording_status or not self._waveform_visible:
                self.hide_overlay()
                return
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

        # Layer 1: background.
        if self._bg_pixmap is None:
            self._bg_pixmap = self._build_bg_pixmap()
        painter.drawPixmap(0, 0, self._bg_pixmap)

        draw_waveform = self._waveform_visible and not self._processing and not self._status_text

        # Layer 2: waveform.
        if draw_waveform:
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

            for i, lv in enumerate(data):
                display_lv = min(0.92, lv / scale)
                h = max(2, int(display_lv * max_half))
                alpha_t = _BAR_EDGE_T[i] * bar_dim[i]
                bar_rect = self._bar_rects[i]
                bar_rect.setRect(_BAR_X0[i], mid - h, BAR_WIDTH, h * 2)
                color = _mix_three_stop_color(
                    self._waveform_color_dark,
                    self._waveform_color_mid,
                    self._waveform_color_light,
                    _waveform_color_position(display_lv / 0.92),
                    int(230 * alpha_t),
                )
                painter.setBrush(color)
                painter.drawRect(bar_rect)

    def _update_recording_status_metrics(self, text: str, color: str = STATUS_PURPLE_MID) -> None:
        if text:
            text_w = QFontMetrics(self._dim_font).horizontalAdvance(text)
            text_cx = WIN_W / 2
            pad = -20
            self._text_dim_left = text_cx - text_w / 2 - pad
            self._text_dim_right = text_cx + text_w / 2 + pad
        else:
            self._text_dim_left = -1.0
            self._text_dim_right = -1.0
        self._rebuild_bar_dim()

    def _processing_color(self) -> QColor:
        elapsed = time.time() - self._proc_start
        t = (math.sin(elapsed * 2 * math.pi) + 1) / 2
        dark = QColor(STATUS_PROCESSING_DARK)
        light = QColor(STATUS_PROCESSING_LIGHT)
        r = int(dark.red() + (light.red() - dark.red()) * t)
        g = int(dark.green() + (light.green() - dark.green()) * t)
        b = int(dark.blue() + (light.blue() - dark.blue()) * t)
        return QColor(r, g, b)

    def _set_status_label(self, text: str = "", color: QColor | None = None, *, on_waveform: bool = False) -> None:
        if not text:
            for label in self._status_shadow_labels:
                label.hide()
                label.setText("")
            self._status_label.hide()
            self._status_label.setText("")
            return

        text_color = (color or QColor(STATUS_PURPLE_MID)).name()
        for label in self._status_shadow_labels:
            label.setText(text)
            if on_waveform:
                label.raise_()
                label.show()
            else:
                label.hide()
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"background:transparent;color:{text_color};font-size:13pt;font-weight:700;"
        )
        self._status_label.raise_()
        self._status_label.show()

    def _make_status_label(self, color: str) -> QLabel:
        label = QLabel(self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(f"background:transparent;color:{color};font-size:13pt;font-weight:700;")
        label.setGeometry(0, 0, WIN_W, WIN_H)
        return label

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
