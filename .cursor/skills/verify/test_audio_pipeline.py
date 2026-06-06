"""測試完整音訊管線：raw int16 frames → process_frames（VAD + 正規化）→ API 辨識。

用法：
    py -3.12 .agents/skills/verify/test_audio_pipeline.py

需要 App 正在執行（透過 debug_query.py eval 取得 API key 與 model），
並使用 tests/golden_speech.wav 作為測試音源。
"""
import io
import json
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, "src")

from ai_whisper.services.audio_service import AudioService

WAV_PATH = "tests/golden_speech.wav"
BLOCK_SIZE = 512  # 與錄音 callback 一致


def load_wav_as_frames(path: str) -> list[np.ndarray]:
    """將 WAV 檔讀成 int16 frames list，模擬錄音 callback 產生的資料。"""
    with wave.open(path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).reshape(-1, 1)  # shape: (N, 1)
    # 切成 BLOCK_SIZE 大小的 chunks，模擬 sounddevice callback
    frames = []
    for i in range(0, len(audio), BLOCK_SIZE):
        chunk = audio[i : i + BLOCK_SIZE]
        if len(chunk) > 0:
            frames.append(chunk)
    return frames


def get_app_config() -> tuple[str, str]:
    """透過 debug server 取得 API key 與 model。"""
    result = subprocess.run(
        ["py", "scripts/debug_query.py", "eval",
         "[self.cfg.apiKey, self.cfg.model]"],
        capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
    )
    data = json.loads(result.stdout)
    if not data.get("ok"):
        print(f"FAIL: 無法取得 App 設定: {data.get('error')}")
        sys.exit(1)
    cfg = data["result"]
    return cfg[0], cfg[1]


def main():
    passed = 0
    failed = 0

    # ── Test 1: process_frames 完整管線（VAD + 正規化 + WAV 轉換）──
    print("Test 1: process_frames 完整管線")
    frames = load_wav_as_frames(WAV_PATH)
    print(f"  載入 {len(frames)} frames (block_size={BLOCK_SIZE})")

    svc = AudioService()
    segment = svc.process_frames(frames, source="stop")

    if segment.wav_bytes is None:
        print(f"  FAIL: process_frames 回傳 None (reason={segment.reason})")
        failed += 1
    else:
        print(f"  OK: 產生 {len(segment.wav_bytes)} bytes WAV (duration={segment.duration:.2f}s)")
        passed += 1

    # ── Test 2: 正規化有作用（用小聲版本測試）──
    print("\nTest 2: 正規化對小聲音訊有作用")
    quiet_frames = [f // 10 for f in frames]  # 音量縮小到 1/10
    orig_peak = int(np.max(np.abs(np.concatenate(quiet_frames))))

    segment_quiet = svc.process_frames(quiet_frames, source="stop",
                                        vad_confidence=0.3,
                                        vad_min_speech_sec=0.2)
    if segment_quiet.wav_bytes is None:
        print(f"  SKIP: 小聲版本未通過 VAD (reason={segment_quiet.reason})")
        print(f"  (原始 peak={orig_peak}，可能太小聲)")
        # 不算失敗，VAD 擋住小聲是預期行為
    else:
        # 解析輸出 WAV，檢查 peak 是否被放大
        with wave.open(io.BytesIO(segment_quiet.wav_bytes), "rb") as wf:
            out_audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        out_peak = int(np.max(np.abs(out_audio)))
        print(f"  輸入 peak={orig_peak} → 輸出 peak={out_peak}")
        if out_peak > orig_peak:
            print(f"  OK: 正規化已放大 ({out_peak/orig_peak:.1f}x)")
            passed += 1
        else:
            print(f"  FAIL: 正規化未生效")
            failed += 1

    # ── Test 3: 正規化後的音訊送 API 仍可正確辨識 ──
    print("\nTest 3: 正規化後送 API 辨識")
    if segment.wav_bytes is None:
        print("  SKIP: 無 WAV bytes")
    else:
        api_key, model = get_app_config()
        from ai_whisper.services.transcription_service import TranscriptionService
        raw_text, clean_text, _ = TranscriptionService.transcribe_clean(
            segment.wav_bytes, api_key, model, []
        )
        if clean_text and len(clean_text.strip()) > 0:
            print(f"  辨識結果: {clean_text}")
            print(f"  OK: API 辨識成功")
            passed += 1
        else:
            print(f"  FAIL: API 回傳空結果 (raw={raw_text!r})")
            failed += 1

    # ── 結果 ──
    print(f"\n{'='*40}")
    total = passed + failed
    print(f"結果: {passed}/{total} 通過")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
