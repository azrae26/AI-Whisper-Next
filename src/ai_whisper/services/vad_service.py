from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..logging_setup import safe_print

SAMPLE_RATE = 16000
VAD_FRAME_SEC = 0.03
VAD_FRAME_THRESHOLD = 450
VAD_SPEECH_RATIO = 0.16
MIN_DURATION_SEC = 0.8
MIN_SPEECH_SEC = 0.35
SILERO_CONFIDENCE_THRESHOLD = 0.6
SILERO_FRAME_SIZE = 512
# v5 ONNX 需要前一幀末尾 64 sample 作為 context 前綴，
# 實際送入模型的 input shape = [1, CONTEXT + FRAME] = [1, 576]
SILERO_CONTEXT_SIZE = 64

_silero_session = None
_silero_available: bool | None = None
_silero_lock = threading.Lock()

_MODEL_FILENAME = "silero_vad.onnx"


@dataclass(frozen=True)
class SpeechAnalysis:
    has_speech: bool
    engine: str
    speech_frames: int = 0
    total_frames: int = 0
    speech_ratio: float = 0.0
    speech_seconds: float = 0.0
    reason: str = ""


def _find_onnx_model() -> str | None:
    """搜尋 silero_vad.onnx，依序檢查：
    1. 專案 assets/（開發環境）
    2. PyInstaller _MEIPASS/assets/（打包後）
    3. torch hub 快取（向下相容舊安裝）
    """
    # 1. 專案 assets/（開發環境：vad_service.py 往上 3 層 = 專案根）
    project_assets = Path(__file__).resolve().parents[3] / "assets" / _MODEL_FILENAME
    if project_assets.exists():
        return str(project_assets)

    # 2. PyInstaller _MEIPASS（打包後）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass) / "assets" / _MODEL_FILENAME
        if meipass_path.exists():
            return str(meipass_path)

    # 3. torch hub 快取（向下相容）
    hub_cache = Path.home() / ".cache" / "torch" / "hub"
    if hub_cache.exists():
        for onnx_file in hub_cache.rglob(_MODEL_FILENAME):
            return str(onnx_file)

    return None


def _load_silero_vad() -> bool:
    global _silero_session, _silero_available
    if _silero_available is not None:
        return _silero_available
    with _silero_lock:
        if _silero_available is not None:
            return _silero_available
        try:
            import onnxruntime as ort

            model_path = _find_onnx_model()
            if model_path is None:
                raise FileNotFoundError(
                    f"找不到 {_MODEL_FILENAME}，請將模型檔放入 assets/ 目錄"
                )
            safe_print(f"[recorder][VAD] 載入 Silero VAD 模型 (ONNX): {model_path}")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _silero_session = ort.InferenceSession(
                model_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            _silero_available = True
            safe_print("[recorder][VAD] Silero VAD 模型載入完成 (ONNX)")
        except Exception as e:
            safe_print(f"[recorder][VAD] ⚠️ Silero VAD 載入失敗，使用 RMS 備援: {e}")
            _silero_available = False
    return _silero_available


def preload_silero_vad() -> None:
    """在背景 thread 預載 Silero VAD，讓第一次錄音不需要等待。"""
    t = threading.Thread(target=_load_silero_vad, daemon=True, name="VAD-preload")
    t.start()


def _build_analysis(
    *,
    engine: str,
    speech_frames: int,
    total_frames: int,
    frame_seconds: float,
    min_speech_sec: float = MIN_SPEECH_SEC,
) -> SpeechAnalysis:
    if total_frames <= 0:
        return SpeechAnalysis(False, engine=engine, reason="沒有足夠音訊幀")

    speech_ratio = speech_frames / total_frames
    speech_seconds = speech_frames * frame_seconds
    if speech_ratio < VAD_SPEECH_RATIO:
        reason = f"語音比例不足 {speech_ratio:.1%} < {VAD_SPEECH_RATIO:.0%}"
        return SpeechAnalysis(False, engine, speech_frames, total_frames, speech_ratio, speech_seconds, reason)
    if speech_seconds < min_speech_sec:
        reason = f"有效語音太短 {speech_seconds:.2f}s < {min_speech_sec:.2f}s"
        return SpeechAnalysis(False, engine, speech_frames, total_frames, speech_ratio, speech_seconds, reason)
    return SpeechAnalysis(True, engine, speech_frames, total_frames, speech_ratio, speech_seconds, "ok")


def analyze_speech(
    audio: np.ndarray,
    confidence_threshold: float = SILERO_CONFIDENCE_THRESHOLD,
    min_speech_sec: float = MIN_SPEECH_SEC,
) -> SpeechAnalysis:
    samples = audio.flatten()
    n_samples = len(samples)
    if n_samples < SILERO_FRAME_SIZE:
        return SpeechAnalysis(False, engine="none", reason="音訊短於 VAD 最小幀")

    if _load_silero_vad():
        audio_f32 = samples.astype(np.float32) / 32768.0
        # ONNX 推論：手動管理 LSTM 隱藏狀態 + context 前綴
        # ⚠ Silero v5 要求每幀前面拼接前一幀末尾 64 sample 作為 context，
        #   即實際 input shape = [1, 576]（64 context + 512 frame）
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros(SILERO_CONTEXT_SIZE, dtype=np.float32)
        sr = np.array(SAMPLE_RATE, dtype=np.int64)
        n_frames = n_samples // SILERO_FRAME_SIZE
        speech_frames = 0
        for i in range(n_frames):
            frame = audio_f32[i * SILERO_FRAME_SIZE:(i + 1) * SILERO_FRAME_SIZE]
            # 拼接 context + frame → [1, 576]
            input_with_context = np.concatenate([context, frame]).reshape(1, -1)
            ort_inputs = {
                "input": input_with_context,
                "state": state,
                "sr": sr,
            }
            out, state = _silero_session.run(None, ort_inputs)
            conf = float(out[0][0])
            if conf >= confidence_threshold:
                speech_frames += 1
            # 保留當前幀末尾作為下一幀的 context
            context = frame[-SILERO_CONTEXT_SIZE:]
        result = _build_analysis(
            engine="Silero",
            speech_frames=speech_frames,
            total_frames=n_frames,
            frame_seconds=SILERO_FRAME_SIZE / SAMPLE_RATE,
            min_speech_sec=min_speech_sec,
        )
        safe_print(
            f"[recorder][VAD] Silero 語音幀 {speech_frames}/{n_frames} ({result.speech_ratio:.1%})，"
            f"有效語音 {result.speech_seconds:.2f}s，信心閾值 {confidence_threshold}，"
            f"最低比例 {VAD_SPEECH_RATIO:.0%}，最低語音 {min_speech_sec:.2f}s"
        )
        return result

    frame_len = int(SAMPLE_RATE * VAD_FRAME_SEC)
    f32 = samples.astype(np.float32)
    n_frames = len(f32) // frame_len
    if n_frames == 0:
        return SpeechAnalysis(False, engine="RMS", reason="沒有足夠音訊幀")
    frames = f32[:n_frames * frame_len].reshape(n_frames, frame_len)
    rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1))
    speech_frames = int(np.sum(rms_per_frame > VAD_FRAME_THRESHOLD))
    result = _build_analysis(
        engine="RMS",
        speech_frames=speech_frames,
        total_frames=n_frames,
        frame_seconds=VAD_FRAME_SEC,
        min_speech_sec=min_speech_sec,
    )
    safe_print(
        f"[recorder][VAD] RMS 語音幀 {speech_frames}/{n_frames} ({result.speech_ratio:.1%})，"
        f"有效語音 {result.speech_seconds:.2f}s，閾值 {VAD_FRAME_THRESHOLD}，"
        f"最低比例 {VAD_SPEECH_RATIO:.0%}，最低語音 {min_speech_sec:.2f}s"
    )
    return result


def has_speech(audio: np.ndarray) -> bool:
    return analyze_speech(audio).has_speech
