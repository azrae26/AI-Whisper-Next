from __future__ import annotations

import time
from typing import Callable

import numpy as np
import sounddevice as sd

from ..logging_setup import now_str, safe_print
from .vad_service import SAMPLE_RATE

TAP_COUNT = 3
# 持續時間上限依峰值分級（每 2k 一級，門檻 = 歷史 MAX(去頭2) + 10ms，單調遞增）
# 峰值:   0–8k   8k–10k  10k–12k  12k+
_TAP_DUR_THRESHOLDS = [
    (8000,  0.110),
    (10000, 0.173),
    (12000, 0.214),
    (float("inf"), 0.214),
]

def _tap_max_duration(peak: float) -> float:
    for limit, dur in _TAP_DUR_THRESHOLDS:
        if peak < limit:
            return dur
    return _TAP_DUR_THRESHOLDS[-1][1]

TAP_MIN_INTERVAL_SEC = 0.18   # 太快（< 180ms）：不是手敲，忽略
TAP_MAX_INTERVAL_SEC = 0.4    # 太慢（> 400ms）：不算連續，重置序列
TAP_RHYTHM_MIN_RATIO = 0.65   # min(ia, ib) / max(ia, ib) must be >= this
TAP_LOG_TAG_WIDTH = len("[tap][sample]")


def _tap_log_prefix(kind: str | None = None, ts: str | None = None) -> str:
    tag = "[tap]" if kind is None else f"[tap][{kind}]"
    prefix = f"{tag:<{TAP_LOG_TAG_WIDTH}}"
    if ts is not None:
        return f"{prefix}[{ts}] "
    return f"{prefix} "


class TapService:
    """Always-on audio monitor that fires a callback when the mic is tapped 3 times
    in a consistent rhythm.  Runs a dedicated InputStream independent of AudioService."""

    def __init__(self, on_triple_tap: Callable[[], None]):
        self._on_triple_tap = on_triple_tap
        self._stream: sd.InputStream | None = None
        self._enabled = False
        self._threshold = 3000.0
        # Detection state — only touched from the single audio-thread callback
        self._above = False
        self._above_start = 0.0
        self._above_peak = 0.0
        self._tap_times: list[float] = []
        self._consecutive_long: int = 0  # counts back-to-back long events; speech → ≥2, hard tap → 1
        # Dim tracking: log lighter taps (below main threshold) for analysis only
        self._dim_above = False
        self._dim_start = 0.0
        self._dim_peak = 0.0

    # ------------------------------------------------------------------
    # Public control API (called from Qt main thread)
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if enabled:
            self._start_stream()
        else:
            self._stop_stream()

    def set_threshold(self, threshold: float) -> None:
        self._threshold = max(100.0, float(threshold))

    def shutdown(self) -> None:
        self._enabled = False
        self._stop_stream()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_stream(self) -> None:
        if self._stream is not None:
            return
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=512,   # ~32 ms chunks for responsive detection
                callback=self._callback,
            )
            self._stream.start()
            safe_print(f"{_tap_log_prefix(ts=now_str())}🎙️ 敲麥監聽已啟動")
        except Exception as e:
            safe_print(f"{_tap_log_prefix(ts=now_str())}❌ 無法開啟監聽 stream: {e}")
            self._stream = None

    def _stop_stream(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._above = False
        self._dim_above = False
        self._consecutive_long = 0
        self._tap_times.clear()
        safe_print(f"{_tap_log_prefix(ts=now_str())}💤 敲麥監聽已停止")


    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        now = time.perf_counter()
        threshold = self._threshold
        dim_threshold = threshold * 0.5

        # --- Dim tracking: log lighter taps that don't reach main threshold ---
        was_dim = self._dim_above
        self._dim_above = rms > dim_threshold and rms <= threshold
        if self._dim_above and not was_dim:
            self._dim_start = now
            self._dim_peak = rms
        elif self._dim_above and was_dim:
            if rms > self._dim_peak:
                self._dim_peak = rms
        elif not self._dim_above and was_dim:
            dur = now - self._dim_start
            if dur < _tap_max_duration(self._dim_peak):
                safe_print(f"{_tap_log_prefix('dim', now_str())}持續={dur*1000:.0f}ms 峰值={self._dim_peak:.0f} ✗弱")

        was_above = self._above
        self._above = rms > threshold

        if self._above and not was_above:
            # 上升緣：記錄候選開始時間與峰值
            self._above_start = now
            self._above_peak = rms
            return

        if self._above and was_above:
            if rms > self._above_peak:
                self._above_peak = rms

        if not self._above and not was_above:
            return

        if self._above and was_above:
            return

        # 下降緣：確認持續時間，過長 → 說話或持續噪音，丟棄
        duration = now - self._above_start
        peak = self._above_peak
        ts = now_str()
        max_dur = _tap_max_duration(peak)
        sample_line = (
            f"{_tap_log_prefix('sample', ts)}持續={duration*1000:.0f}ms 峰值={peak:.0f} "
            f"{'✓' if duration < max_dur else '✗長'}"
        )
        if duration >= max_dur:
            safe_print(sample_line)
            self._consecutive_long += 1
            if self._consecutive_long >= 2:
                # 連續兩個長聲音 → 語音或持續噪音 → 重置序列
                self._tap_times.clear()
            # 單一長聲音可能是硬敲麥克風造成的共鳴，不立即重置
            return

        # 短事件：清除連續長聲音計數
        self._consecutive_long = 0

        # 用上升緣時間作為敲擊時間點
        tap_time = self._above_start

        reset_note = ""
        if self._tap_times:
            gap = tap_time - self._tap_times[-1]
            if gap < TAP_MIN_INTERVAL_SEC:
                safe_print(
                    f"{sample_line} ⏱️ 防抖略過（距上次 {gap*1000:.0f}ms < "
                    f"{TAP_MIN_INTERVAL_SEC*1000:.0f}ms）"
                )
                return
            if gap > TAP_MAX_INTERVAL_SEC:
                safe_print(f"{_tap_log_prefix()}⏱️ 間隔過長重置（{gap*1000:.0f}ms）")
                self._tap_times.clear()

        self._tap_times.append(tap_time)
        safe_print(f"{sample_line} 🎯 敲擊 #{len(self._tap_times)}")

        if len(self._tap_times) >= TAP_COUNT:
            t1, t2, t3 = self._tap_times[-3], self._tap_times[-2], self._tap_times[-1]
            ia, ib = t2 - t1, t3 - t2
            ratio = min(ia, ib) / max(ia, ib) if max(ia, ib) > 0 else 1.0
            if ratio >= TAP_RHYTHM_MIN_RATIO:
                self._tap_times.clear()
                safe_print(
                    f"{_tap_log_prefix(ts=ts)}🔔 三連敲觸發（間隔 {ia*1000:.0f}ms / {ib*1000:.0f}ms，"
                    f"一致性 {ratio:.2f}）"
                )
                self._on_triple_tap()
            else:
                safe_print(
                    f"{_tap_log_prefix(ts=ts)}⚠️ 節奏不一致（間隔 {ia*1000:.0f}ms / {ib*1000:.0f}ms，"
                    f"一致性 {ratio:.2f}），忽略"
                )
