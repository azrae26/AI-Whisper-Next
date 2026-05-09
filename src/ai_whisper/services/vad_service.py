from __future__ import annotations

from dataclasses import dataclass

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

_silero_model = None
_silero_available: bool | None = None


@dataclass(frozen=True)
class SpeechAnalysis:
    has_speech: bool
    engine: str
    speech_frames: int = 0
    total_frames: int = 0
    speech_ratio: float = 0.0
    speech_seconds: float = 0.0
    reason: str = ""


def _load_silero_vad() -> bool:
    global _silero_model, _silero_available
    if _silero_available is not None:
        return _silero_available
    try:
        import torch
        safe_print("[recorder][VAD] 載入 Silero VAD 模型...")
        model, _ = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        model.eval()
        _silero_model = model
        _silero_available = True
        safe_print("[recorder][VAD] Silero VAD 模型載入完成")
    except Exception as e:
        safe_print(f"[recorder][VAD] ⚠️ Silero VAD 載入失敗，使用 RMS 備援: {e}")
        _silero_available = False
    return _silero_available


def _build_analysis(
    *,
    engine: str,
    speech_frames: int,
    total_frames: int,
    frame_seconds: float,
) -> SpeechAnalysis:
    if total_frames <= 0:
        return SpeechAnalysis(False, engine=engine, reason="沒有足夠音訊幀")

    speech_ratio = speech_frames / total_frames
    speech_seconds = speech_frames * frame_seconds
    if speech_ratio < VAD_SPEECH_RATIO:
        reason = f"語音比例不足 {speech_ratio:.1%} < {VAD_SPEECH_RATIO:.0%}"
        return SpeechAnalysis(False, engine, speech_frames, total_frames, speech_ratio, speech_seconds, reason)
    if speech_seconds < MIN_SPEECH_SEC:
        reason = f"有效語音太短 {speech_seconds:.2f}s < {MIN_SPEECH_SEC:.2f}s"
        return SpeechAnalysis(False, engine, speech_frames, total_frames, speech_ratio, speech_seconds, reason)
    return SpeechAnalysis(True, engine, speech_frames, total_frames, speech_ratio, speech_seconds, "ok")


def analyze_speech(audio: np.ndarray) -> SpeechAnalysis:
    samples = audio.flatten()
    n_samples = len(samples)
    if n_samples < SILERO_FRAME_SIZE:
        return SpeechAnalysis(False, engine="none", reason="音訊短於 VAD 最小幀")

    if _load_silero_vad():
        import torch
        audio_f32 = samples.astype(np.float32) / 32768.0
        _silero_model.reset_states()
        n_frames = n_samples // SILERO_FRAME_SIZE
        speech_frames = 0
        with torch.no_grad():
            for i in range(n_frames):
                frame = audio_f32[i * SILERO_FRAME_SIZE:(i + 1) * SILERO_FRAME_SIZE]
                tensor = torch.from_numpy(frame).unsqueeze(0)
                confidence = _silero_model(tensor, SAMPLE_RATE).item()
                if confidence >= SILERO_CONFIDENCE_THRESHOLD:
                    speech_frames += 1
        result = _build_analysis(
            engine="Silero",
            speech_frames=speech_frames,
            total_frames=n_frames,
            frame_seconds=SILERO_FRAME_SIZE / SAMPLE_RATE,
        )
        safe_print(
            f"[recorder][VAD] Silero 語音幀 {speech_frames}/{n_frames} ({result.speech_ratio:.1%})，"
            f"有效語音 {result.speech_seconds:.2f}s，信心閾值 {SILERO_CONFIDENCE_THRESHOLD}，"
            f"最低比例 {VAD_SPEECH_RATIO:.0%}，最低語音 {MIN_SPEECH_SEC:.2f}s"
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
    )
    safe_print(
        f"[recorder][VAD] RMS 語音幀 {speech_frames}/{n_frames} ({result.speech_ratio:.1%})，"
        f"有效語音 {result.speech_seconds:.2f}s，閾值 {VAD_FRAME_THRESHOLD}，"
        f"最低比例 {VAD_SPEECH_RATIO:.0%}，最低語音 {MIN_SPEECH_SEC:.2f}s"
    )
    return result


def has_speech(audio: np.ndarray) -> bool:
    return analyze_speech(audio).has_speech
