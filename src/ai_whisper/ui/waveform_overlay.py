from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import socket
import time

from ..logging_setup import safe_print
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer

_HOSTNAME = socket.gethostname()


def _screen_key(screen_name: str) -> str:
    """Unique key per computer+screen, used for position persistence."""
    return f"{_HOSTNAME}/{screen_name}"
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPixmap, QPen
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
OVERLAY_RAISE_Y = 50
STATUS_PURPLE_MID = "#e2b8ff"
WAVEFORM_COLOR_DARK = "#22D3EE"
WAVEFORM_COLOR_MID = "#52E1F6"
WAVEFORM_COLOR_LIGHT = "#99f3ff"
STATUS_PROCESSING_LIGHT = "#edd2ff"
STATUS_PROCESSING_DARK = "#d493ff"
STATUS_FONT_SIZE = 14




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

# L1 perf: Pre-computed 256-entry waveform colour gradient LUT.
# Maps gradient position (0..255) → (r, g, b) using the same 3-stop ramp
# as _mix_three_stop_color(dark, mid, light, t).  paintEvent indexes into
# this table instead of computing per-bar colour blends every frame.
_LUT_SIZE = 256
_LUT_DARK = QColor(WAVEFORM_COLOR_DARK)
_LUT_MID = QColor(WAVEFORM_COLOR_MID)
_LUT_LIGHT = QColor(WAVEFORM_COLOR_LIGHT)
_WAVEFORM_GRADIENT_LUT: list[tuple[int, int, int]] = []
for _i in range(_LUT_SIZE):
    _t = _i / (_LUT_SIZE - 1)
    if _t < 0.5:
        _left, _right, _lt = _LUT_DARK, _LUT_MID, _t * 2
    else:
        _left, _right, _lt = _LUT_MID, _LUT_LIGHT, (_t - 0.5) * 2
    _WAVEFORM_GRADIENT_LUT.append((
        int(_left.red() + (_right.red() - _left.red()) * _lt),
        int(_left.green() + (_right.green() - _left.green()) * _lt),
        int(_left.blue() + (_right.blue() - _left.blue()) * _lt),
    ))
del _i, _t, _left, _right, _lt, _LUT_DARK, _LUT_MID, _LUT_LIGHT
STATUS_TIMER_ARM_DELAY_MS = 120

# --- Drag/reset button panel (separate interactive window on top of transparent overlay) ---
_BTN_W = 22
_BTN_H = 22
_BTN_PAD_R = 6    # px from right edge of main overlay
_BTN_GAP = 4      # gap between the two buttons
_BTN_TOTAL_H = _BTN_H * 2 + _BTN_GAP
_BTN_X = WIN_W - _BTN_PAD_R - _BTN_W
_RESET_BTN_Y = (WIN_H - _BTN_TOTAL_H) // 2
_DRAG_BTN_Y = _RESET_BTN_Y + _BTN_H + _BTN_GAP
_HOVER_ZONE_X = WIN_W - 50  # rightmost 50 px triggers panel visibility
# Panel window geometry (in main overlay logical coords → panel-local coords)
_PANEL_X = _BTN_X          # panel left = main BTN_X
_PANEL_Y = _RESET_BTN_Y    # panel top  = main RESET_BTN_Y
_PANEL_W = _BTN_W          # 22 px wide
_PANEL_H = _BTN_TOTAL_H    # 48 px tall
# Button rects in PANEL-LOCAL coordinates
_PNL_RESET = QRect(0, 0, _BTN_W, _BTN_H)
_PNL_DRAG  = QRect(0, _BTN_H + _BTN_GAP, _BTN_W, _BTN_H)


class _OverlayButtons(QWidget):
    """Small interactive window that hosts drag-handle + reset buttons.
    Sits on top of the fully transparent WaveformOverlay, so it can receive
    real OS mouse events — no polling, no hacks."""

    def __init__(self, main: "WaveformOverlay") -> None:
        super().__init__(None)
        self._main = main
        self._syncing = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(_PANEL_W, _PANEL_H)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _fix_win11_frame(self)

    def sync_to_main(self) -> None:
        """Reposition this panel to align with the main overlay's button area."""
        if not self._syncing:
            self._syncing = True
            self.move(self._main.x() + _PANEL_X, self._main.y() + _PANEL_Y)
            self._syncing = False

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self._syncing:
            # Panel moved via OS-native HTCAPTION drag — pull main overlay along.
            self._syncing = True
            self._main.move(self.x() - _PANEL_X, self.y() - _PANEL_Y)
            self._syncing = False

    def nativeEvent(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = ctypes.cast(int(message), ctypes.POINTER(ctypes.wintypes.MSG)).contents
            if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                # Return MA_NOACTIVATE so panel never steals focus, but click IS still delivered.
                return True, 3
            if msg.message == 0x0201:  # WM_LBUTTONDOWN — handle reset here (reliable for Tool windows)
                if _PNL_RESET.contains(self.mapFromGlobal(QCursor.pos())):
                    self._main._do_reset()
                return True, 0
            if msg.message == 0x0084:  # WM_NCHITTEST
                local = self.mapFromGlobal(QCursor.pos())
                if _PNL_DRAG.contains(local):
                    return True, 2   # HTCAPTION — OS handles drag natively
                return True, 1       # HTCLIENT (1, not 0) — reset button / gap
            if msg.message == 0x0232:  # WM_EXITSIZEMOVE — drag ended
                self._main._clamp_to_screen()
                self.sync_to_main()
                key = _screen_key(self._main._screen_name)
                pos = self._main.pos()
                self._main._custom_pos = pos
                self._main._overlay_positions[key] = {"x": pos.x(), "y": pos.y()}
                safe_print(f"[overlay] 位置儲存 screen={self._main._screen_name} x={pos.x()} y={pos.y()}")
                if self._main._on_pos_changed:
                    self._main._on_pos_changed(key, pos.x(), pos.y())
        return super().nativeEvent(eventType, message)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Button backgrounds
        painter.setPen(Qt.PenStyle.NoPen)
        for rect in (_PNL_RESET, _PNL_DRAG):
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 5, 5)
            painter.fillPath(path, QColor(255, 255, 255, 30))
        icon_color = QColor(255, 255, 255, 200)
        # Reset icon — CCW rotation arrow
        pen = QPen(icon_color, 1.2, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rcx = _BTN_W / 2.0
        rcy = _BTN_H / 2.0
        r = 3.85
        painter.drawArc(QRectF(rcx - r, rcy - r, r * 2, r * 2), 200 * 16, 260 * 16)
        ex = rcx + r * math.cos(math.radians(100))
        ey = rcy - r * math.sin(math.radians(100))
        painter.drawLine(QPointF(ex, ey), QPointF(ex + 2.4, ey + 1.3))
        painter.drawLine(QPointF(ex, ey), QPointF(ex + 1.75, ey - 2.1))
        # Drag icon — 2×3 grip dots
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(icon_color)
        dcx = _BTN_W / 2.0
        dcy = _BTN_H + _BTN_GAP + _BTN_H / 2.0
        sq, gap = 2.8, 1.4
        x0 = dcx - sq - gap / 2
        y0 = dcy - sq * 1.5 - gap
        for col in range(2):
            for row in range(3):
                painter.drawRoundedRect(
                    QRectF(x0 + col * (sq + gap), y0 + row * (sq + gap), sq, sq), 0.85, 0.85
                )


class WaveformOverlay(QWidget):
    def __init__(self, overlay_positions: dict | None = None, on_pos_changed=None):
        super().__init__(None)
        self._on_pos_changed = on_pos_changed
        self._overlay_positions: dict = overlay_positions or {}
        self._custom_pos: QPoint | None = None  # set per-screen in _anchor_to_cursor_screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput  # passes ALL mouse events (incl. wheel) through
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
        self._dim_font = QFont("Microsoft JhengHei UI", STATUS_FONT_SIZE)  # used for QFontMetrics; kept separate from _paint_font
        self._dim_font.setBold(True)
        self._color_proc_dark = QColor(STATUS_PROCESSING_DARK)
        self._color_proc_light = QColor(STATUS_PROCESSING_LIGHT)
        # Status text state — rendered directly in paintEvent (no child QLabel/shadow)
        self._status_label_text = ""
        self._status_label_color = QColor(STATUS_PURPLE_MID)
        self._status_label_on_waveform = False
        self._shadow_cache_key = ""   # text that the cached shadow was built for
        self._shadow_cache_px: QPixmap | None = None  # blurred shadow pixmap
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_processing)

        # Cache expensive objects to avoid allocating them on every paintEvent
        self._paint_font = QFont("Microsoft JhengHei UI", STATUS_FONT_SIZE)
        self._paint_font.setBold(True)
        self._bg_pixmap: QPixmap | None = None  # built on first paint; WIN_W/H are constants
        self._bar_dim: list[float] = [1.0] * BAR_COUNT  # recomputed only when text_dim changes

        # Pre-allocated QRect list for batched drawRects — avoids per-frame allocation
        self._bar_rects: list[QRect] = [QRect() for _ in range(BAR_COUNT)]

        # L2: Prime window surface off-screen to cache font glyphs & bg pixmap
        # without any visible flash (WA_DontShowOnScreen prevents actual display).
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.show()
        self.repaint()
        self.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)

        # Hover polling – shows/hides the button panel when cursor enters right-edge zone.
        self._btn_panel = _OverlayButtons(self)
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._check_button_hover)
        # Started only when overlay becomes visible; stopped in hide_overlay().

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
        self._hover_timer.start(50)
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
        self._hover_timer.start(50)
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
        if text == "識別中":
            self._proc_start = time.time()
            self._set_status_label(text, self._processing_color(), on_waveform=self._waveform_visible)
            self._timer.start(50)
        else:
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
        self._hover_timer.start(50)
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
        self._hover_timer.stop()
        self._btn_panel.hide()
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
            self._hover_timer.start(50)
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
            self._hover_timer.start(50)
            self.raise_()
        else:
            self._processing = True
            self._hide_after_recording_status = False
            self._proc_start = time.time()
            self._set_status_label("識別中", self._processing_color(), on_waveform=False)
            if not self._timer.isActive():
                self._timer.start(50)
            self.show()
            self._hover_timer.start(50)
            self.raise_()
        self.update()

    def set_levels(self, levels: list[float]) -> None:
        if not self._waveform_visible or self._processing:
            return
        self._levels = levels  # already trimmed to BAR_COUNT by get_waveform()
        self.update()

    def _clamp_to_screen(self) -> None:
        """Ensure the overlay stays within the anchored screen (no cross-screen drag)."""
        screen = self._anchored_screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        x = max(rect.left(), min(self.x(), rect.right() - WIN_W))
        y = max(rect.top(), min(self.y(), rect.bottom() - WIN_H))
        new_pos = QPoint(x, y)
        if new_pos != self.pos():
            self.move(new_pos)
            if self._custom_pos is not None:
                self._custom_pos = new_pos
        if self._btn_panel.isVisible():
            self._btn_panel.sync_to_main()

    def _position_at_cursor_screen(self) -> None:
        if self._custom_pos is not None:
            self.move(self._custom_pos)
            self._clamp_to_screen()
        else:
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
        if self._btn_panel.isVisible():
            self._btn_panel.sync_to_main()

    def _do_reset(self) -> None:
        """Reset overlay position to screen default (called by button panel)."""
        self._custom_pos = None
        key = _screen_key(self._screen_name)
        self._overlay_positions.pop(key, None)
        safe_print(f"[overlay] 位置重置 screen={self._screen_name}")
        self._position_at_cursor_screen()
        if self._on_pos_changed:
            self._on_pos_changed(key, -1, -1)

    def _anchor_to_cursor_screen(self) -> None:
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QApplication.primaryScreen()
        self._screen_name = screen.name() if screen is not None else ""
        # Load saved position for this computer+screen
        key = _screen_key(self._screen_name)
        pos_data = self._overlay_positions.get(key)
        if pos_data:
            self._custom_pos = QPoint(int(pos_data["x"]), int(pos_data["y"]))
        else:
            self._custom_pos = None

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
        elif self._recording_status_text == "識別中":
            self._set_status_label("識別中", self._processing_color(), on_waveform=self._waveform_visible)
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

        # Clear previous frame first (WA_TranslucentBackground doesn't guarantee this on Windows).
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

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

            lut = _WAVEFORM_GRADIENT_LUT
            lut_max = _LUT_SIZE - 1
            for i, lv in enumerate(data):
                display_lv = min(0.92, lv / scale)
                h = max(2, int(display_lv * max_half))
                alpha_t = _BAR_EDGE_T[i] * bar_dim[i]
                bar_rect = self._bar_rects[i]
                bar_rect.setRect(_BAR_X0[i], mid - h, BAR_WIDTH, h * 2)
                # L1: LUT lookup instead of per-bar _mix_three_stop_color
                idx = int(_waveform_color_position(display_lv / 0.92) * lut_max)
                r, g, b = lut[idx]
                painter.setBrush(QColor(r, g, b, int(230 * alpha_t)))
                painter.drawRect(bar_rect)

        # Layer 3: status text with painted shadow (no QLabel/QGraphicsDropShadowEffect).
        status_text = self._status_label_text
        if status_text:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setFont(self._paint_font)
            text_rect = QRectF(0, 0, WIN_W, WIN_H)
            align = Qt.AlignmentFlag.AlignCenter
            if self._status_label_on_waveform:
                # Soft glow shadow: render text black → downscale/upscale blur → cache.
                # Regenerate only when text changes (color doesn't affect shadow).
                if status_text != self._shadow_cache_key:
                    self._shadow_cache_key = status_text
                    spx = QPixmap(WIN_W, WIN_H)
                    spx.fill(Qt.GlobalColor.transparent)
                    sp = QPainter(spx)
                    sp.setFont(self._paint_font)
                    sp.setPen(QColor(0, 0, 0, 210))
                    sp.drawText(text_rect, align, status_text)
                    sp.end()
                    # Two-pass downscale → upscale = Gaussian-like blur (~8px radius)
                    s1 = spx.scaled(
                        max(1, WIN_W // 2), max(1, WIN_H // 2),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    s2 = s1.scaled(
                        max(1, WIN_W // 4), max(1, WIN_H // 4),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._shadow_cache_px = s2.scaled(
                        WIN_W, WIN_H,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                painter.drawPixmap(0, 0, self._shadow_cache_px)
            # Foreground text
            painter.setPen(self._status_label_color)
            painter.drawText(text_rect, align, status_text)

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
        dark = self._color_proc_dark
        light = self._color_proc_light
        r = int(dark.red() + (light.red() - dark.red()) * t)
        g = int(dark.green() + (light.green() - dark.green()) * t)
        b = int(dark.blue() + (light.blue() - dark.blue()) * t)
        return QColor(r, g, b)

    def _set_status_label(self, text: str = "", color: QColor | None = None, *, on_waveform: bool = False) -> None:
        """Store status text state and schedule repaint. Rendered in paintEvent."""
        self._status_label_text = text
        self._status_label_color = color or QColor(STATUS_PURPLE_MID)
        self._status_label_on_waveform = on_waveform
        self.update()

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

    # ------------------------------------------------------------------
    # Right-edge drag / reset buttons
    # ------------------------------------------------------------------

    def _check_button_hover(self) -> None:
        """Show/hide the interactive button panel based on cursor position."""
        if not self.isVisible():
            self._btn_panel.hide()
            return
        # Physical-pixel check avoids DPI mapping issues with WindowTransparentForInput.
        cursor_pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor_pt))
        win_rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(win_rect))
        dpr = self.devicePixelRatio()
        cx, cy = cursor_pt.x, cursor_pt.y
        hover = (
            win_rect.left + round(_HOVER_ZONE_X * dpr) <= cx < win_rect.right
            and win_rect.top <= cy < win_rect.bottom
        )
        if hover and not self._btn_panel.isVisible():
            self._btn_panel.sync_to_main()
            self._btn_panel.show()
            self._btn_panel.raise_()
        elif not hover and self._btn_panel.isVisible():
            self._btn_panel.hide()

    def _arm_recording_status_timeout(self, token: int, text: str, duration_ms: int) -> None:
        if token != self._recording_status_token or self._recording_status_text != text:
            return
        self._recording_status_until = time.time() + duration_ms / 1000
        if not self._timer.isActive():
            self._timer.start(50)
