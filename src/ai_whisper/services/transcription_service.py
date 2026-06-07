from __future__ import annotations

import io
import queue
import threading
import time

from openai import OpenAI

from ..logging_setup import log_prefix, now_str, safe_print
from ..models import TextCorrection
from ..text_processing import normalize_transcription_text


class TranscriptionService:
    _clients: dict[str, OpenAI] = {}
    _clients_lock = threading.Lock()

    @classmethod
    def _get_client(cls, api_key: str) -> OpenAI:
        with cls._clients_lock:
            if api_key not in cls._clients:
                cls._clients[api_key] = OpenAI(api_key=api_key)
            return cls._clients[api_key]

    @classmethod
    def warmup_connection(cls, api_key: str) -> None:
        """Pre-establish TCP+TLS connection to OpenAI so first transcription is faster."""
        if not api_key:
            return
        try:
            t0 = time.perf_counter()
            client = cls._get_client(api_key)
            client.models.retrieve("whisper-1")
            safe_print(f"{log_prefix('[transcriber]', now_str())}🔌 HTTP 連線預熱完成 ({time.perf_counter() - t0:.2f}s)")
        except Exception as e:
            safe_print(f"{log_prefix('[transcriber]', now_str())}⚠️ HTTP 連線預熱失敗: {e}")

    @classmethod
    def transcribe_raw(cls, wav_bytes: bytes, api_key: str, model: str) -> str:
        audio_file = io.BytesIO(wav_bytes)
        t0 = time.perf_counter()
        response = cls._get_client(api_key).audio.transcriptions.create(
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
        safe_print(f"{log_prefix('[transcriber]', now_str())}⏱️ API 耗時: {time.perf_counter() - t0:.2f}s")
        return response.text.strip()

    @classmethod
    def transcribe_with_retry(cls, wav_bytes: bytes, api_key: str, model: str, timeout: float = 2.5) -> str:
        result_q: queue.Queue = queue.Queue()
        both_started = False

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
            safe_print(f"{log_prefix('[main]', now_str())}⚠️ API 超過 {timeout}s 未回應，重試中…")
            threading.Thread(target=_call, args=(2,), daemon=True, name="TranscribeAttempt2").start()
            both_started = True
            status, payload, attempt = result_q.get(timeout=30)

        if attempt == 2:
            safe_print(f"{log_prefix('[main]', now_str())}🔄 使用重試結果")
        if status == "ok":
            return payload
        # ⚠️ 如果兩軌都已啟動，第一個回報 error 時不應立刻放棄——
        # 第二軌可能幾百毫秒後就會成功。只有兩軌都 error 才拋出。
        if both_started:
            safe_print(f"{log_prefix('[main]', now_str())}⚠️ Attempt {attempt} 失敗，等待另一軌…")
            try:
                status2, payload2, attempt2 = result_q.get(timeout=28)
                if status2 == "ok":
                    safe_print(f"{log_prefix('[main]', now_str())}🔄 使用 Attempt {attempt2} 的結果")
                    return payload2
            except queue.Empty:
                pass
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
