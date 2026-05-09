from __future__ import annotations

import datetime
import io
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from ..logging_setup import safe_print
from .vad_service import MIN_DURATION_SEC, SAMPLE_RATE, analyze_speech

CHANNELS = 1
SILENCE_LEVEL = 0.06


@dataclass
class AudioSegment:
    wav_bytes: bytes | None
    reason: str = ""
    duration: float = 0.0
    error: str = ""


class AudioService:
    def __init__(self):
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._waveform: list[float] = []
        self._wf_lock = threading.Lock()
        self._segment_samples = 0
        self._silence_chunks = 0
        self._chunk_samples = 0
        self._stream_start_time = 0.0
        self._first_cb_logged = False

    def start(self) -> bool:
        with self._lock:
            if self._recording:
                return False
            self._frames = []
            with self._wf_lock:
                self._waveform = []
            self._segment_samples = 0
            self._silence_chunks = 0
            self._chunk_samples = 0
            self._recording = True

            if self._stream is not None:
                safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 🚀 預熱命中，直接開始錄音')
                return True

        perf = time.perf_counter
        self._first_cb_logged = False

        def _callback(indata, frames, time_info, status):
            if not self._first_cb_logged:
                self._first_cb_logged = True
                delay_ms = (perf() - self._stream_start_time) * 1000
                safe_print(
                    f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] '
                    f'🎤 第一包音訊到達，麥克風實際開啟延遲 {delay_ms:.1f}ms'
                )
            with self._lock:
                if not self._recording:
                    return
                self._frames.append(indata.copy())
                chunk_len = len(indata)
                if not self._chunk_samples:
                    self._chunk_samples = chunk_len
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                level = min(1.0, rms / 5000)
                self._segment_samples += chunk_len
                if level < SILENCE_LEVEL:
                    self._silence_chunks += 1
                else:
                    self._silence_chunks = 0
            with self._wf_lock:
                self._waveform.append(level)
                if len(self._waveform) > 200:
                    self._waveform = self._waveform[-200:]

        try:
            t0 = time.perf_counter()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=_callback,
            )
            t1 = time.perf_counter()
            safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] ⏱️ InputStream() {(t1 - t0) * 1000:.1f}ms')
            self._stream_start_time = time.perf_counter()
            self._stream.start()
            t2 = time.perf_counter()
            safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] ⏱️ stream.start() {(t2 - t1) * 1000:.1f}ms')
            safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 🎙️ 錄音就緒，總初始化 {(t2 - t0) * 1000:.1f}ms')
            return True
        except Exception as e:
            safe_print(f"[recorder][start] ❌ 錄音裝置錯誤: {e}")
            with self._lock:
                self._recording = False
            return False

    def stop_capture(self) -> list[np.ndarray] | None:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            frames = self._frames
            self._frames = []
            self._segment_samples = 0
        return frames if frames else None

    def process_frames(
        self,
        frames: list[np.ndarray] | None,
        source: str,
        vad_confidence: float = 0.6,
        vad_min_speech_sec: float = 0.35,
    ) -> AudioSegment:
        if not frames:
            return AudioSegment(None, reason="empty")
        audio_data = np.concatenate(frames, axis=0)
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_DURATION_SEC:
            safe_print(f"[recorder][{source}] 錄音太短 ({duration:.2f}s)，略過" if source == "stop" else f"[recorder][{source}] 段落太短 ({duration:.2f}s)，略過")
            return AudioSegment(None, reason="too_short", duration=duration)
        speech = analyze_speech(audio_data, confidence_threshold=vad_confidence, min_speech_sec=vad_min_speech_sec)
        if not speech.has_speech:
            msg = "不送出辨識" if source == "stop" else "略過"
            safe_print(f"[recorder][{source}] ❌ VAD 未達有效語音門檻，{msg}（{speech.reason}）")
            return AudioSegment(None, reason="no_speech", duration=duration)
        if source == "flush":
            safe_print(f"[recorder][flush] ✅ 取出 {duration:.1f}s 音訊段落")
        return AudioSegment(self._to_wav_bytes(audio_data), reason="ok", duration=duration)

    def flush_capture(self) -> list[np.ndarray] | None:
        with self._lock:
            if not self._recording or not self._frames:
                return None
            frames = self._frames
            self._frames = []
            self._segment_samples = 0
        return frames

    def reset_silence(self) -> None:
        with self._lock:
            self._silence_chunks = 0

    def shutdown(self) -> None:
        with self._lock:
            self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        safe_print(f'[recorder][{datetime.datetime.now().strftime("%H:%M:%S")}] 💤 預熱 stream 已關閉')

    def get_waveform(self) -> list[float]:
        with self._wf_lock:
            return self._waveform.copy()

    def get_accumulated_seconds(self) -> float:
        with self._lock:
            return self._segment_samples / SAMPLE_RATE

    def get_silence_seconds(self) -> float:
        with self._lock:
            chunk_sec = self._chunk_samples / SAMPLE_RATE if self._chunk_samples else 0.032
            return self._silence_chunks * chunk_sec

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @staticmethod
    def _to_wav_bytes(audio_data: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        buf.seek(0)
        return buf.read()
