from __future__ import annotations

import io
import queue
import threading
import time

from openai import OpenAI

from ..logging_setup import now_str, safe_print
from ..models import TextCorrection
from ..text_processing import normalize_transcription_text


class TranscriptionService:
    @staticmethod
    def transcribe_raw(wav_bytes: bytes, api_key: str, model: str) -> str:
        client = OpenAI(api_key=api_key)
        audio_file = io.BytesIO(wav_bytes)
        t0 = time.perf_counter()
        response = client.audio.transcriptions.create(
            model=model,
            file=("audio.wav", audio_file, "audio/wav"),
            language="zh",
            prompt=(
                "以下是近距離麥克風收錄的繁體中文語音，內容可能夾雜英文單字。"
                "只轉寫清楚、主要、近距離的人聲。"
                "如果音訊只有背景噪音、音樂、鍵盤聲、遠處或旁人的聲音、"
                "含糊不可辨識的聲音，請回傳空字串，不要猜測或補字。"
            ),
        )
        safe_print(f"[transcriber][{now_str()}] ⏱️ API 耗時: {time.perf_counter() - t0:.2f}s")
        return response.text.strip()

    @classmethod
    def transcribe_with_retry(cls, wav_bytes: bytes, api_key: str, model: str, timeout: float = 2.5) -> str:
        result_q: queue.Queue = queue.Queue()

        def _call(attempt: int) -> None:
            try:
                text = cls.transcribe_raw(wav_bytes, api_key, model)
                result_q.put(("ok", text, attempt))
            except Exception as e:
                result_q.put(("error", str(e), attempt))

        threading.Thread(target=_call, args=(1,), daemon=True, name="TranscribeAttempt1").start()
        try:
            status, payload, attempt = result_q.get(timeout=timeout)
        except queue.Empty:
            safe_print(f"[main][{now_str()}] ⚠️ API 超過 {timeout}s 未回應，重試中…")
            threading.Thread(target=_call, args=(2,), daemon=True, name="TranscribeAttempt2").start()
            status, payload, attempt = result_q.get()

        if attempt == 2:
            safe_print(f"[main][{now_str()}] 🔄 使用重試結果")
        if status == "ok":
            return payload
        raise Exception(payload)

    @classmethod
    def transcribe_clean(
        cls,
        wav_bytes: bytes,
        api_key: str,
        model: str,
        corrections: list[TextCorrection],
    ) -> tuple[str, str, float]:
        raw = cls.transcribe_with_retry(wav_bytes, api_key, model)
        received_at = time.perf_counter()
        clean = normalize_transcription_text(raw, corrections)
        return raw, clean, received_at
