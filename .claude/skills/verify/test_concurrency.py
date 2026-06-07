"""測試修復 1, 2, 3, 5 的針對性場景驗證。

修復 1：線程池飢餓死鎖 — 驗證 _process_final_audio 用 Thread 不被 worker 滿阻塞
修復 2：雙軌 Retry — 驗證第一軌 error 後第二軌仍能救場
修復 3：UIA Worker Queue 污染 — 驗證舊 worker 恢復後不會搶新 Queue
修復 5：SettingsStore 併發寫入 — 驗證加鎖後不會資料覆蓋
"""
import sys
import os
import time
import threading
import queue
import concurrent.futures
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
#  測試 3：UIA Worker Queue 污染
# ═══════════════════════════════════════════════════════════

class _BuggyExecutor:
    """重現舊版 paste_service.py _UIATransactionExecutor：worker_loop 用 self._queue"""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._worker = None

    def _ensure_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._queue = queue.Queue()
                self._worker = threading.Thread(
                    target=self._worker_loop, daemon=True, name="BuggyWorker")
                self._worker.start()

    def _worker_loop(self):
        while True:
            item = self._queue.get()  # BUG: 動態查找
            if item is None:
                break
            fn, result_future = item
            try:
                result_future.set_result(fn())
            except Exception as e:
                result_future.set_exception(e)

    def execute(self, fn, default, timeout=0.5):
        self._ensure_worker()
        future = concurrent.futures.Future()
        self._queue.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                try:
                    self._queue.put_nowait(None)
                except Exception:
                    pass
                self._worker = None
            return default


class _FixedExecutor:
    """修復後：worker_loop 啟動時 q = self._queue 存為 local。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._worker = None

    def _ensure_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._queue = queue.Queue()
                self._worker = threading.Thread(
                    target=self._worker_loop, daemon=True, name="FixedWorker")
                self._worker.start()

    def _worker_loop(self):
        q = self._queue  # FIX: 捕獲為 local
        while True:
            item = q.get()
            if item is None:
                break
            fn, result_future = item
            try:
                result_future.set_result(fn())
            except Exception as e:
                result_future.set_exception(e)

    def execute(self, fn, default, timeout=0.5):
        self._ensure_worker()
        future = concurrent.futures.Future()
        self._queue.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                try:
                    self._queue.put_nowait(None)
                except Exception:
                    pass
                self._worker = None
            return default


def _run_queue_test(executor_cls):
    """重現時序：Worker-A 卡住 → 超時換 Queue → Worker-A 恢復 → 觀察讀哪個 Queue。"""
    executor = executor_cls()
    blocker = threading.Event()
    initial_queue = executor._queue
    worker_a_info = []

    def blocking_fn():
        blocker.wait(timeout=5)
        worker_a_info.append({"same_as_initial": executor._queue is initial_queue})
        return "done"

    executor.execute(blocking_fn, "timeout", timeout=0.5)
    new_queue = executor._queue

    blocker.set()
    time.sleep(0.3)

    executor._ensure_worker()
    stolen_by = []
    probe_future = concurrent.futures.Future()
    executor._queue.put((lambda: stolen_by.append(threading.current_thread().name) or "probe", probe_future))
    time.sleep(0.3)

    return {
        "queue_replaced": new_queue is not initial_queue,
        "worker_a_info": worker_a_info,
        "stolen_by": stolen_by,
    }


def test_queue_pollution():
    print("\n--- 測試 3a：舊版 Queue 污染（確認 bug 存在）---")
    r = _run_queue_test(_BuggyExecutor)
    report("Queue 超時後被替換", r["queue_replaced"])
    report(
        "舊版 Worker-A 恢復後讀到新 Queue（bug）",
        r["worker_a_info"] and not r["worker_a_info"][0]["same_as_initial"],
        f"queue 已變: {r['worker_a_info']}",
    )

    print("\n--- 測試 3b：新版 Queue 隔離（修復正確）---")
    r = _run_queue_test(_FixedExecutor)
    report("Queue 超時後被替換", r["queue_replaced"])
    if r["stolen_by"]:
        report(
            "新版 probe 只被 Worker-B 消費",
            "FixedWorker" in r["stolen_by"][0],
            f"消費者={r['stolen_by'][0]}",
        )
    else:
        report("新版 probe 應被 Worker-B 消費", False, "無消費者")


# ═══════════════════════════════════════════════════════════
#  測試 5：SettingsStore 併發寫入
# ═══════════════════════════════════════════════════════════

def _make_unlocked_store(path):
    """建立一個沒有鎖的 SettingsStore 模擬（重現舊行為）。"""
    class UnlockedStore:
        def __init__(self, p):
            self.path = Path(p)
            self._config = {}
        def save(self, updates: dict):
            merged = dict(self._config)
            merged.update(updates)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(self.path.parent))
            try:
                import os as _os
                with _os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                _os.replace(tmp, self.path)
            finally:
                import os as _os
                if _os.path.exists(tmp):
                    try: _os.remove(tmp)
                    except OSError: pass
            self._config = merged
    return UnlockedStore(path)


def _make_locked_store(path):
    """建立一個有鎖的 SettingsStore 模擬（修復後行為）。"""
    class LockedStore:
        def __init__(self, p):
            self.path = Path(p)
            self._config = {}
            self._lock = threading.Lock()
        def save(self, updates: dict):
            with self._lock:
                # 加鎖後：先從磁碟重新讀取最新狀態，再合併
                if self.path.exists():
                    with open(self.path, "r", encoding="utf-8") as f:
                        self._config = json.load(f)
                merged = dict(self._config)
                merged.update(updates)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(self.path.parent))
                try:
                    import os as _os
                    with _os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(merged, f, ensure_ascii=False, indent=2)
                    _os.replace(tmp, self.path)
                finally:
                    import os as _os
                    if _os.path.exists(tmp):
                        try: _os.remove(tmp)
                        except OSError: pass
                self._config = merged
    return LockedStore(path)


def _run_concurrent_save(store_factory, tmp_dir):
    """兩個 thread 同時 save 不同的 key，看最終結果是否都保留。"""
    config_path = os.path.join(tmp_dir, "test_config.json")
    store = store_factory(config_path)
    barrier = threading.Barrier(2)
    errors = []

    def writer_a():
        try:
            barrier.wait(timeout=2)
            for i in range(20):
                store.save({"key_a": f"value_a_{i}"})
        except Exception as e:
            errors.append(e)

    def writer_b():
        try:
            barrier.wait(timeout=2)
            for i in range(20):
                store.save({"key_b": f"value_b_{i}"})
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer_a, daemon=True)
    t2 = threading.Thread(target=writer_b, daemon=True)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    # 讀取最終結果
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            final = json.load(f)
    else:
        final = {}

    has_a = "key_a" in final
    has_b = "key_b" in final
    return {"final": final, "has_both": has_a and has_b, "has_a": has_a, "has_b": has_b, "errors": errors}


def test_settings_concurrent_write():
    print("\n--- 測試 5a：舊版無鎖併發寫入（確認資料覆蓋 bug）---")
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_concurrent_save(_make_unlocked_store, tmp)
        # 無鎖時：兩個 writer 各自讀到舊的 _config 再 merge，
        # 後寫的會覆蓋前寫的 key → 最終結果通常只剩一個 key
        report(
            "舊版無鎖：併發寫入導致資料覆蓋（bug）",
            not r["has_both"],
            f"has_a={r['has_a']}, has_b={r['has_b']}, final_keys={list(r['final'].keys())}",
        )

    print("\n--- 測試 5b：新版加鎖併發寫入（修復正確）---")
    with tempfile.TemporaryDirectory() as tmp:
        r = _run_concurrent_save(_make_locked_store, tmp)
        report(
            "新版加鎖：兩個 key 都保留",
            r["has_both"],
            f"has_a={r['has_a']}, has_b={r['has_b']}, final_keys={list(r['final'].keys())}",
        )


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    test_thread_pool_starvation()
    test_retry_first_error_second_ok()
    test_retry_both_error()
    test_retry_normal_path()
    test_queue_pollution()
    test_settings_concurrent_write()

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
