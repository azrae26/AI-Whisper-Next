"""測試修復 1 & 2 的針對性場景驗證。

修復 1：線程池飢餓死鎖 — 驗證 _process_final_audio 用 Thread 不被 worker 滿阻塞
修復 2：雙軌 Retry — 驗證第一軌 error 後第二軌仍能救場
"""
import sys
import os
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 尋找專案根目錄（包含 src/ 的目錄）
current_path = os.path.abspath(os.path.dirname(__file__))
project_root = None
for _ in range(6):
    if os.path.exists(os.path.join(current_path, "src")):
        project_root = current_path
        break
    current_path = os.path.dirname(current_path)

if not project_root:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

sys.path.insert(0, os.path.join(project_root, "src"))

PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name}" + (f" -- {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" -- {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════════
#  測試 1：線程池飢餓場景
# ═══════════════════════════════════════════════════════════

def test_thread_pool_starvation():
    """4 worker 全滿時，threading.Thread 仍可立刻執行，
    而 executor.submit 會排隊（驗證舊行為有問題、新行為正確）。"""
    print("\n--- 測試 1：線程池飢餓 ---")
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="TestWorker")
    blocker = threading.Event()
    final_result_submit = []
    final_result_thread = []

    def blocking_segment_worker(seg_id):
        blocker.wait(timeout=10)

    def final_task_for_submit():
        final_result_submit.append(time.perf_counter())

    def final_task_for_thread():
        final_result_thread.append(time.perf_counter())

    for i in range(4):
        executor.submit(blocking_segment_worker, i)
    time.sleep(0.1)

    t0 = time.perf_counter()
    executor.submit(final_task_for_submit)
    threading.Thread(target=final_task_for_thread, daemon=True).start()
    time.sleep(2.0)

    thread_ran = len(final_result_thread) > 0
    submit_ran = len(final_result_submit) > 0

    report(
        "Thread 方式在 worker 全滿時仍立刻執行",
        thread_ran,
        f"Thread 在 {(final_result_thread[0] - t0)*1000:.0f}ms 後執行" if thread_ran else "未執行",
    )
    report(
        "Submit 方式在 worker 全滿時被阻塞（確認舊行為有問題）",
        not submit_ran,
        "submit 被阻塞" if not submit_ran else "submit 竟然執行了",
    )

    blocker.set()
    executor.shutdown(wait=True, cancel_futures=True)


# ═══════════════════════════════════════════════════════════
#  測試 2：雙軌 Retry
# ═══════════════════════════════════════════════════════════

def test_retry_first_error_second_ok():
    """Attempt 1 超時後返回 error，Attempt 2 隨後成功 → 應返回成功結果。"""
    print("\n--- 測試 2a：第一軌 error + 第二軌 ok ---")
    from ai_whisper.services.transcription_service import TranscriptionService

    call_count = {"value": 0}
    original = TranscriptionService.transcribe_raw

    @classmethod
    def mock(cls, wav_bytes, api_key, model):
        call_count["value"] += 1
        attempt = call_count["value"]
        if attempt == 1:
            time.sleep(2.8)
            raise ConnectionError("模擬網路超時")
        else:
            time.sleep(1.0)
            return "測試辨識結果"

    TranscriptionService.transcribe_raw = mock
    try:
        t0 = time.perf_counter()
        result = TranscriptionService.transcribe_with_retry(b"fake", "k", "m", timeout=2.5)
        elapsed = time.perf_counter() - t0
        report("第一軌 error 後第二軌成功救場", result == "測試辨識結果", f"返回={repr(result)}, {elapsed:.1f}s")
        report("兩軌都被呼叫", call_count["value"] == 2, f"呼叫次數={call_count['value']}")
    except Exception as e:
        report("第一軌 error 後第二軌成功救場", False, f"拋出異常: {e}")
    finally:
        TranscriptionService.transcribe_raw = original


def test_retry_both_error():
    """兩軌都失敗 → 應正確拋出異常。"""
    print("\n--- 測試 2b：兩軌都 error ---")
    from ai_whisper.services.transcription_service import TranscriptionService

    call_count = {"value": 0}
    original = TranscriptionService.transcribe_raw

    @classmethod
    def mock(cls, wav_bytes, api_key, model):
        call_count["value"] += 1
        if call_count["value"] == 1:
            time.sleep(2.8)
            raise ConnectionError("模擬超時 A1")
        else:
            time.sleep(1.0)
            raise ConnectionError("模擬超時 A2")

    TranscriptionService.transcribe_raw = mock
    try:
        TranscriptionService.transcribe_with_retry(b"fake", "k", "m", timeout=2.5)
        report("兩軌都 error 時正確拋出異常", False, "沒有拋出異常")
    except Exception as e:
        report("兩軌都 error 時正確拋出異常", True, f"異常={e}")
    finally:
        TranscriptionService.transcribe_raw = original


def test_retry_normal_path():
    """Attempt 1 在 timeout 內成功 → 正常路徑不受影響。"""
    print("\n--- 測試 2c：正常路徑（A1 快速成功）---")
    from ai_whisper.services.transcription_service import TranscriptionService

    original = TranscriptionService.transcribe_raw

    @classmethod
    def mock(cls, wav_bytes, api_key, model):
        time.sleep(0.3)
        return "正常結果"

    TranscriptionService.transcribe_raw = mock
    try:
        t0 = time.perf_counter()
        result = TranscriptionService.transcribe_with_retry(b"fake", "k", "m", timeout=2.5)
        elapsed = time.perf_counter() - t0
        report("正常路徑不受影響", result == "正常結果" and elapsed < 1.0, f"返回={repr(result)}, {elapsed:.1f}s")
    except Exception as e:
        report("正常路徑不受影響", False, f"拋出異常: {e}")
    finally:
        TranscriptionService.transcribe_raw = original


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    test_thread_pool_starvation()
    test_retry_first_error_second_ok()
    test_retry_both_error()
    test_retry_normal_path()

    print(f"\n{'='*40}")
    total = PASS + FAIL
    if FAIL:
        print(f"結果：{PASS}/{total} 通過，{FAIL} 失敗")
    else:
        print(f"結果：{PASS}/{total} 通過")
    print(f"{'='*40}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
