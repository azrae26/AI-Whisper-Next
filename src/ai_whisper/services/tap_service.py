from __future__ import annotations

import time
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

from ..logging_setup import log_prefix, now_str, safe_print
from .vad_service import SAMPLE_RATE

TAP_COUNT = 3
# 持續時間上限依峰值分級
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

TAP_MIN_INTERVAL_SEC = 0.18
TAP_MAX_INTERVAL_SEC = 0.4

# ══════════════════════════════════════════════════════════════
#  信心評分參數
# ══════════════════════════════════════════════════════════════

CONF_TRIGGER = 58           # 觸發所需最低信心分數

# ① 峰值強度 (0-30)
CONF_PEAK_TABLE = [
    (3000, 30), (2000, 25), (1000, 20), (700, 10), (0, 0),
]
# ② 前靜音 (0-25)  ── #1 前 CONF_SILENCE_WIN 秒內的 dim 事件數
CONF_SILENCE_WIN   = 0.50
CONF_SILENCE_TABLE = [(0, 25), (1,  0), (2, 0)]  # 1 個 dim 不加分（加嚴）
# ③ 雜訊比 (0-30)  ── 窗口內最高 dim 峰值 ÷ 均峰值
CONF_DIM_WIN   = 0.15
CONF_DIM_TABLE = [(0.30, 30), (0.50, 20), (0.70, 10), (1.00, 0)]
# ④ 節奏一致性 (0-15)
CONF_RHYTHM_TABLE = [(0.90, 15), (0.80, 10), (0.65, 5), (0.00, 0)]
# ⑤ 底噪扣分 (0 ~ -20)  ── 敲擊間靜音的最大 RMS
CONF_FLOOR_TABLE = [(150, 0), (300, -10), (float("inf"), -20)]
# ⑥ ZCR 扣分 (0 ~ -20)  ── 零交叉率（語音 > 敲擊）
CONF_ZCR_TABLE   = [(0.118, 0), (0.135, -8), (float("inf"), -20)]
# ⑦ 上升時間扣分 (0 ~ -25)  ── 3敲平均 attack time（ms），物理敲擊 < 6ms，語音 > 7ms
CONF_ATT_TABLE   = [(6, 0), (12, -15), (float("inf"), -25)]
# ⑧ SNR 扣分 (0 ~ -20)  ── avg_peak / 2s基線，語音中敲擊 SNR 低
CONF_SNR_TABLE   = [(5, -20), (8, -10), (float("inf"), 0)]

# ══════════════════════════════════════════════════════════════

def _score_ge(value: float, table: list) -> int:
    for threshold, pts in table:
        if value >= threshold:
            return pts
    return 0

def _score_lt(value: float, table: list) -> int:
    for limit, pts in table:
        if value < limit:
            return pts
    return table[-1][1]



class TapService:
    """Always-on audio monitor that fires a callback when the mic is tapped 3 times
    in a consistent rhythm.  Runs a dedicated InputStream independent of AudioService."""

    def __init__(self, on_triple_tap: Callable[[], None]):
        self._on_triple_tap = on_triple_tap
        self._stream: sd.InputStream | None = None
        self._enabled = False
        self._threshold = 3000.0
        # Detection state
        self._above = False
        self._above_start = 0.0
        self._above_peak = 0.0
        self._tap_times: list[float] = []
        self._tap_peaks: list[float] = []
        self._consecutive_long: int = 0
        # Dim tracking
        self._dim_above = False
        self._dim_start = 0.0
        self._dim_peak = 0.0
        self._recent_dims: deque = deque()  # (time, peak)，rolling ~2s
        # Extra metrics
        self._inter_floors: list[float] = []
        self._floor_tracking = False
        self._floor_max: float = 0.0
        self._baseline: deque = deque()             # (time, rms) rolling 2s
        self._zcr_buffer: deque = deque(maxlen=64)  # per-block ZCR
        self._event_samples: list = []              # 當次 above 期間的原始 PCM blocks
        self._audio_buffer: deque = deque()         # (block_time, samples) rolling 1.5s，供固定窗口計算
        self._tap_kcfsc: list = []                  # 每個有效敲擊的 (K, CF, SC, fK, fCF, fSC)

    # ------------------------------------------------------------------
    # Public API
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
                blocksize=512,
                callback=self._callback,
            )
            self._stream.start()
            safe_print(f"{log_prefix('[tap]', now_str())}🎙️ 敲麥監聽已啟動")
        except Exception as e:
            safe_print(f"{log_prefix('[tap]', now_str())}❌ 無法開啟監聽 stream: {e}")
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
        self._recent_dims.clear()
        self._baseline.clear()
        self._zcr_buffer.clear()
        self._audio_buffer.clear()
        self._reset_tap_sequence()
        safe_print(f"{log_prefix('[tap]', now_str())}💤 敲麥監聽已停止")

    def _reset_tap_sequence(self) -> None:
        self._tap_times.clear()
        self._tap_peaks.clear()
        self._inter_floors.clear()
        self._tap_kcfsc.clear()
        self._floor_tracking = False
        self._floor_max = 0.0

    def _compute_confidence(self, t1: float, t2: float, t3: float,
                             p1: float, p2: float, p3: float) -> tuple[int, dict]:
        avg_peak = (p1 + p2 + p3) / 3

        # ① 峰值強度
        peak_pts = _score_ge(avg_peak, CONF_PEAK_TABLE)

        # ② 前靜音
        dims_before = [p for t, p in self._recent_dims if t1 - CONF_SILENCE_WIN <= t < t1]
        n_before = len(dims_before)
        silence_pts = 0
        for max_n, pts in CONF_SILENCE_TABLE:
            if n_before <= max_n:
                silence_pts = pts
                break

        # ③ 雜訊比
        win_start, win_end = t1 - CONF_DIM_WIN, t3 + CONF_DIM_WIN
        dims_win = [p for t, p in self._recent_dims if win_start <= t <= win_end]
        if dims_win:
            max_dim = max(dims_win)
            noise_ratio = max_dim / avg_peak if avg_peak > 0 else 1.0
        else:
            max_dim, noise_ratio = 0.0, 0.0
        dim_pts = _score_lt(noise_ratio, CONF_DIM_TABLE)

        # ④ 節奏
        ia, ib = t2 - t1, t3 - t2
        rhythm = min(ia, ib) / max(ia, ib) if max(ia, ib) > 0 else 1.0
        rhythm_pts = _score_ge(rhythm, CONF_RHYTHM_TABLE)

        # ⑤ 底噪扣分
        f12 = self._inter_floors[-2] if len(self._inter_floors) >= 2 else -1.0
        f23 = self._inter_floors[-1] if len(self._inter_floors) >= 1 else -1.0
        floor_val = max(f12, f23)
        floor_pts = _score_lt(floor_val, CONF_FLOOR_TABLE) if floor_val >= 0 else 0

        # ⑥ ZCR 扣分
        avg_zcr = sum(self._zcr_buffer) / len(self._zcr_buffer) if self._zcr_buffer else -1.0
        zcr_pts = _score_lt(avg_zcr, CONF_ZCR_TABLE) if avg_zcr >= 0 else 0

        total = peak_pts + silence_pts + dim_pts + rhythm_pts + floor_pts + zcr_pts
        detail = dict(
            total=total, rhythm=rhythm,
            peak=peak_pts, silence=silence_pts, dim=dim_pts, rhythm_pts=rhythm_pts,
            floor=floor_pts, floor_val=round(floor_val),
            zcr=zcr_pts, zcr_val=round(avg_zcr, 3),
            ia_ms=round(ia * 1000), ib_ms=round(ib * 1000),
        )
        return total, detail

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        now = time.perf_counter()
        threshold = self._threshold
        dim_threshold = threshold * 0.5

        # --- ZCR per block ---
        flat = indata[:, 0].astype(np.float32) if indata.ndim > 1 else indata.flatten().astype(np.float32)
        signs = np.sign(flat)
        zcr = float(np.sum(np.abs(np.diff(signs))) / max(2 * (len(flat) - 1), 1))
        self._zcr_buffer.append(zcr)

        # --- Rolling 2-s RMS baseline ---
        self._baseline.append((now, rms))
        while self._baseline and now - self._baseline[0][0] > 2.0:
            self._baseline.popleft()

        # --- Rolling 1.5-s 原始音訊緩衝（供固定窗口計算）---
        self._audio_buffer.append((now, flat.copy()))
        while self._audio_buffer and now - self._audio_buffer[0][0] > 1.5:
            self._audio_buffer.popleft()

        # --- Dim tracking ---
        was_dim = self._dim_above
        self._dim_above = dim_threshold < rms <= threshold
        if self._dim_above and not was_dim:
            self._dim_start = now
            self._dim_peak = rms
        elif self._dim_above and was_dim:
            if rms > self._dim_peak:
                self._dim_peak = rms
        elif not self._dim_above and was_dim:
            dur = now - self._dim_start
            if dur < _tap_max_duration(self._dim_peak):
                safe_print(f"{log_prefix('[tap][dim]', now_str())}持續={dur*1000:.0f}ms 峰值={self._dim_peak:.0f} ✗弱")
                # 加入 rolling dim 緩衝（用於信心評分）
                self._recent_dims.append((self._dim_start, self._dim_peak))
                while self._recent_dims and now - self._recent_dims[0][0] > 2.0:
                    self._recent_dims.popleft()

        was_above = self._above
        self._above = rms > threshold

        if self._above and not was_above:
            # 上升緣：儲存敲擊間底噪，開始累積事件樣本
            if self._floor_tracking and self._tap_times:
                self._inter_floors.append(self._floor_max)
                self._floor_max = 0.0
            self._above_start = now
            self._above_peak = rms
            self._event_samples = [flat.copy()]
            return

        if self._above and was_above:
            if rms > self._above_peak:
                self._above_peak = rms
            self._event_samples.append(flat.copy())

        if not self._above and not was_above:
            if self._floor_tracking and rms > self._floor_max:
                self._floor_max = rms
            return

        if self._above and was_above:
            return

        # 下降緣：確認持續時間
        duration = now - self._above_start
        peak = self._above_peak
        ts = now_str()
        max_dur = _tap_max_duration(peak)

        # --- 事件級指標：above-threshold 窗口 ---
        if self._event_samples:
            evt = np.concatenate(self._event_samples).astype(np.float32)
            self._event_samples = []
            evt_rms  = float(np.sqrt(np.mean(evt ** 2)))
            evt_mean = float(np.mean(evt))
            evt_std  = float(np.std(evt))
            kurt  = float(np.mean(((evt - evt_mean) / evt_std) ** 4)) - 3.0 if evt_std > 0 else 0.0
            crest = float(np.max(np.abs(evt))) / (evt_rms + 1e-9)
            fft_m = np.abs(np.fft.rfft(evt))
            freqs = np.fft.rfftfreq(len(evt), 1.0 / SAMPLE_RATE)
            centroid = float(np.sum(freqs * fft_m) / (np.sum(fft_m) + 1e-9))
            # Attack time: 10%→90% rise (samples within event)
            env_abs = np.abs(evt)
            pk_idx  = int(np.argmax(env_abs))
            pk_val  = env_abs[pk_idx]
            lo_idx  = next((i for i in range(pk_idx) if env_abs[i] >= 0.1 * pk_val), 0)
            hi_idx  = next((i for i in range(lo_idx, pk_idx + 1) if env_abs[i] >= 0.9 * pk_val), pk_idx)
            att_ms  = (hi_idx - lo_idx) / SAMPLE_RATE * 1000.0
        else:
            kurt, crest, centroid, att_ms = 0.0, 0.0, 0.0, 0.0

        # --- 事件級指標：固定窗口（onset-20ms ~ end+20ms，含前後靜音）---
        WIN_PRE, WIN_POST = 0.020, 0.020
        fw_blocks = [s for t, s in self._audio_buffer
                     if self._above_start - WIN_PRE <= t <= now + WIN_POST]
        if fw_blocks:
            fw      = np.concatenate(fw_blocks).astype(np.float32)
            fw_rms  = float(np.sqrt(np.mean(fw ** 2)))
            fw_mean = float(np.mean(fw))
            fw_std  = float(np.std(fw))
            fkurt   = float(np.mean(((fw - fw_mean) / fw_std) ** 4)) - 3.0 if fw_std > 0 else 0.0
            fcrest  = float(np.max(np.abs(fw))) / (fw_rms + 1e-9)
            fw_fft  = np.abs(np.fft.rfft(fw))
            fw_frq  = np.fft.rfftfreq(len(fw), 1.0 / SAMPLE_RATE)
            fcentroid = float(np.sum(fw_frq * fw_fft) / (np.sum(fw_fft) + 1e-9))
            # Spectral flatness（接近1=寬頻/敲擊，接近0=諧波/語音）
            flat  = float(np.exp(np.mean(np.log(fw_fft + 1e-9))) / (np.mean(fw_fft) + 1e-9))
            # HF ratio：>4kHz 能量佔比
            hi_mask = fw_frq > 4000
            hf_ratio = float(np.sum(fw_fft[hi_mask] ** 2) / (np.sum(fw_fft ** 2) + 1e-9))
        else:
            fkurt, fcrest, fcentroid, flat, hf_ratio = 0.0, 0.0, 0.0, 0.0, 0.0

        sample_line = (
            f"{log_prefix('[tap][sample]', ts)}持續={duration*1000:.0f}ms 峰值={peak:.0f} "
            f"{'✓' if duration < max_dur else '✗長'}"
            f" K={kurt:.1f} CF={crest:.1f} SC={centroid:.0f}Hz Att={att_ms:.1f}ms"
            f" | fK={fkurt:.1f} fCF={fcrest:.1f} fSC={fcentroid:.0f}Hz"
            f" fFlat={flat:.3f} fHF={hf_ratio:.3f}"
        )
        if duration >= max_dur:
            safe_print(sample_line)
            self._consecutive_long += 1
            if self._consecutive_long >= 2:
                self._reset_tap_sequence()
            return

        self._consecutive_long = 0
        tap_time = self._above_start

        if self._tap_times:
            gap = tap_time - self._tap_times[-1]
            if gap < TAP_MIN_INTERVAL_SEC:
                safe_print(
                    f"{sample_line} ⏱️ 防抖略過（距上次 {gap*1000:.0f}ms < "
                    f"{TAP_MIN_INTERVAL_SEC*1000:.0f}ms）"
                )
                return
            if gap > TAP_MAX_INTERVAL_SEC:
                safe_print(f"{log_prefix('[tap]', ts)}⏱️ 間隔過長重置（{gap*1000:.0f}ms）")
                self._reset_tap_sequence()

        self._tap_times.append(tap_time)
        self._tap_peaks.append(peak)
        self._tap_kcfsc.append((kurt, crest, centroid, fkurt, fcrest, fcentroid, flat, hf_ratio, att_ms))
        self._floor_tracking = True
        self._floor_max = 0.0
        safe_print(f"{sample_line} 🎯 敲擊 #{len(self._tap_times)}")

        if len(self._tap_times) >= TAP_COUNT:
            t1, t2, t3 = self._tap_times[-3], self._tap_times[-2], self._tap_times[-1]
            p1, p2, p3 = self._tap_peaks[-3], self._tap_peaks[-2], self._tap_peaks[-1]

            total, d = self._compute_confidence(t1, t2, t3, p1, p2, p3)
            old_pass = d['rhythm'] >= 0.65   # 舊節奏門檻（僅供 log 比對）
            new_pass = total >= CONF_TRIGGER

            # ZCR / baseline / floors 供 log 輸出
            avg_zcr = (sum(self._zcr_buffer) / len(self._zcr_buffer)
                       if self._zcr_buffer else 0.0)
            baseline = (sum(r for _, r in self._baseline) / len(self._baseline)
                        if self._baseline else 1.0)
            f12 = round(self._inter_floors[-2]) if len(self._inter_floors) >= 2 else -1
            f23 = round(self._inter_floors[-1]) if len(self._inter_floors) >= 1 else -1

            # 三敲一致性（最後3下各指標的平均與標準差）
            import statistics as _st
            tap3 = self._tap_kcfsc[-3:] if len(self._tap_kcfsc) >= 3 else self._tap_kcfsc
            def _avg(idx): return sum(x[idx] for x in tap3) / len(tap3) if tap3 else 0.0
            def _std(idx): return _st.pstdev([x[idx] for x in tap3]) if len(tap3) > 1 else 0.0
            std_k, std_cf = _std(0), _std(1)
            avg_fk,  std_fk  = _avg(3), _std(3)
            avg_fcf, std_fcf = _avg(4), _std(4)
            avg_fsc, std_fsc = _avg(5), _std(5)
            avg_flat, std_flat = _avg(6), _std(6)
            avg_hf,  std_hf  = _avg(7), _std(7)
            avg_att, std_att = _avg(8), _std(8)
            peaks_3 = self._tap_peaks[-3:] if len(self._tap_peaks) >= 3 else self._tap_peaks
            avg_pk  = sum(peaks_3) / len(peaks_3) if peaks_3 else 0.0
            cv_peak = (_st.pstdev(peaks_3) / (avg_pk + 1e-9)) if len(peaks_3) > 1 else 0.0
            snr     = avg_pk / (baseline + 1e-9)

            # ⑦ 上升時間扣分
            att_pts = _score_lt(avg_att, CONF_ATT_TABLE)
            # ⑧ SNR 扣分
            snr_pts = _score_lt(snr, CONF_SNR_TABLE)
            total  += att_pts + snr_pts
            new_pass = total >= CONF_TRIGGER

            consist_str = (
                f" stdK={std_k:.1f} stdCF={std_cf:.1f} CV_pk={cv_peak:.2f} SNR={snr:.1f}"
                f" | fK={avg_fk:.1f}±{std_fk:.1f} fCF={avg_fcf:.1f}±{std_fcf:.1f}"
                f" fSC={avg_fsc:.0f}±{std_fsc:.0f} fFlat={avg_flat:.3f}±{std_flat:.3f}"
                f" fHF={avg_hf:.3f}±{std_hf:.3f} Att={avg_att:.1f}±{std_att:.1f}ms"
                f" [⑦Att={att_pts}({avg_att:.1f}ms) ⑧SNR={snr_pts}({snr:.1f})]"
            )

            if new_pass:
                self._reset_tap_sequence()
                safe_print(
                    f"{log_prefix('[tap]', ts)}🔔 三連敲觸發（間隔 {d['ia_ms']}ms / {d['ib_ms']}ms，"
                    f"一致性 {d['rhythm']:.2f}）"
                    f" 底噪={f12}/{f23} 基線={baseline:.0f} ZCR={avg_zcr:.3f}"
                    f"{consist_str}"
                    f" [信心={total} 峰={d['peak']} 靜={d['silence']} 噪={d['dim']}"
                    f" 律={d['rhythm_pts']} 底={d['floor']}({d['floor_val']}) ZCR={d['zcr']}({d['zcr_val']})"
                    f" Att={att_pts} SNR={snr_pts}]"
                )
                self._on_triple_tap()
            else:
                self._reset_tap_sequence()
                reason = "信心不足" if old_pass else "節奏+信心不足"
                safe_print(
                    f"{log_prefix('[tap]', ts)}🚫 三連敲被過濾（{reason}，信心={total}/{CONF_TRIGGER}）"
                    f" 底噪={f12}/{f23} 基線={baseline:.0f} ZCR={avg_zcr:.3f}"
                    f"{consist_str}"
                    f" [峰={d['peak']} 靜={d['silence']} 噪={d['dim']}"
                    f" 律={d['rhythm_pts']} 底={d['floor']}({d['floor_val']}) ZCR={d['zcr']}({d['zcr_val']})"
                    f" Att={att_pts} SNR={snr_pts}]"
                )
