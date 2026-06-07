r"""AI Whisper Next — 貼上功能測試腳本

透過 Debug Server (127.0.0.1:47643) 遠端執行，驗證貼上管線各環節：
  0. App 存活檢查
  1. 剪貼簿備份還原迴路（不觸發 Ctrl+V）
  2. 修飾鍵狀態檢查
  3. 前景視窗資訊
  4. 完整貼上端到端測試（觸發 Ctrl+V，文字會貼到前景視窗）
  5. H11: SendInput 驗證 backoff 邊界測試
  6. H9/H12: Timer debounce 引用檢查
  7. H6: UIA timeout 保護常數
  8. L7: ThreadPoolExecutor workers 數量
  9. L8: PasteService shutdown 方法

使用方式（從專案根目錄）：
  py -3.12 .agents/skills/verify/test_paste.py
"""
from __future__ import annotations

import json
import socket
import sys
import time

# Windows cp950 不支援 emoji，強制 UTF-8 輸出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOST = "127.0.0.1"
PORT = 47643


def query(method: str, params: dict | None = None, timeout: float = 10) -> dict:
    req: dict = {"method": method}
    if params:
        req["params"] = params
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as s:
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return json.loads(buf.decode("utf-8").strip())
    except ConnectionRefusedError:
        return {"ok": False, "error": "App not running (connection refused)"}
    except TimeoutError:
        return {"ok": False, "error": "Connection timed out"}
    except OSError as e:
        return {"ok": False, "error": f"Connection failed: {e}"}


def eval_expr(expr: str, timeout: float = 10) -> dict:
    return query("eval", {"expr": expr}, timeout=timeout)


def log_to_app(msg: str) -> None:
    """透過 eval 讓 App 主程序 print，訊息會寫入 App 的日誌檔。"""
    eval_expr(f"print('[test] ' + {repr(msg)})")



def get_result(r: dict):
    """Debug server 回傳 result 或 value，統一取值。"""
    if "result" in r:
        return r["result"]
    if "value" in r:
        return r["value"]
    return r.get("error", "?")


def p(ok: bool, label: str, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    icon = "[v]" if ok else "[X]"
    msg = f"  {icon} {status} | {label}"
    if detail:
        msg += f": {detail}"
    print(msg)
    return ok


def main():
    print("=" * 60)
    print("AI Whisper Next — Paste Test")
    print("=" * 60)
    total = 0
    passed = 0

    # ── 0. 基本存活檢查 ──
    print("\n-- 0. Ping --")
    total += 1
    r = query("ping")
    if p(r.get("ok", False), "App alive"):
        passed += 1
    else:
        print("  App not running, aborting.")
        sys.exit(1)

    log_to_app("=" * 40)
    log_to_app("Paste Test START")

    # ── 1. 剪貼簿備份還原迴路 ──
    print("\n-- 1. Clipboard backup/restore round-trip --")

    # 1a. 備份當前剪貼簿
    total += 1
    r = eval_expr("self.paste._save_clipboard_all()")
    backup_val = get_result(r)
    ok_backup = r.get("ok", False) and backup_val is not None
    if p(ok_backup, "Clipboard backup", f"{len(backup_val) if isinstance(backup_val, list) else 0} formats"):
        passed += 1

    # 1b. 寫入測試文字
    test_text = "AI_WHISPER_TEST_" + str(int(time.time()))
    total += 1
    r = eval_expr(f"self.paste._set_clipboard_verified('{test_text}')")
    ok_set = r.get("ok", False) and get_result(r) is True
    if p(ok_set, "Set clipboard text", test_text):
        passed += 1

    # 1c. 讀回驗證
    total += 1
    r = eval_expr("self.paste._read_clipboard_text()")
    read_val = get_result(r)
    ok_read = r.get("ok", False) and read_val == test_text
    if p(ok_read, "Read clipboard verify", f"got='{read_val}', expected='{test_text}'"):
        passed += 1

    # 1d. 還原（用 walrus operator 避免多 statement）
    total += 1
    r = eval_expr(
        "(bk := self.paste._save_clipboard_all()) is not None "
        "and self.paste._set_clipboard_verified('__RESTORE_TEST__') "
        "and self.paste._restore_clipboard_verified(bk)"
    )
    ok_restore = r.get("ok", False) and get_result(r) is True
    if p(ok_restore, "Clipboard restore verified", str(get_result(r))):
        passed += 1

    # ── 2. 修飾鍵狀態 ──
    print("\n-- 2. Modifier key state --")
    total += 1
    r = eval_expr("self.input.modifier_state_summary()")
    if p(r.get("ok", False), "Modifier state summary", str(get_result(r))):
        passed += 1

    total += 1
    r = eval_expr("self.input.ctrl_state_down()")
    ctrl_down = get_result(r)
    if p(r.get("ok", False), "Ctrl state (idle should be False)", str(ctrl_down)):
        passed += 1

    # ── 3. 前景視窗資訊 ──
    print("\n-- 3. Foreground window --")
    total += 1
    r = eval_expr("list(self.paste._foreground_window())")
    if p(r.get("ok", False), "Foreground window info", str(get_result(r))[:80]):
        passed += 1

    # ── 4. 完整貼上端到端測試 ──
    print("\n-- 4. Full paste end-to-end (text will be pasted to foreground) --")
    total += 1
    r = eval_expr(
        "self.paste.paste_text('TEST_PASTE', delay_ms=50, end_prefix='', preserve_ctrl_modifier=False) or 'queued'"
    )
    queued = r.get("ok", False)
    if p(queued, "paste_text queued", str(get_result(r))):
        passed += 1

    # 等待非同步貼上完成
    print("  Waiting 4s for async paste...")
    time.sleep(4)

    # 讀 log 確認有貼上相關輸出
    total += 1
    r = eval_expr(
        "[l for l in open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').read().splitlines()[-25:] if any(k in l for k in ('CLIP','PASTE','Ctrl+V','TEXT input'))]"
    )
    if r.get("ok", False):
        log_lines = get_result(r)
        if log_lines:
            if p(True, f"Paste activity in log ({len(log_lines)} lines)"):
                passed += 1
            for line in log_lines[-5:]:
                print(f"    {line.strip()[:100]}")
        else:
            p(False, "No paste activity in log")
    else:
        p(False, "Read log failed", str(get_result(r))[:80])

    # ── 5. H11: SendInput 驗證 backoff 邊界測試 ──
    print("\n-- 5. H11: SendInput verify backoff edge cases --")

    # 5a. before_readable=False → 立即返回 (True, 'unreadable')
    total += 1
    r = eval_expr('self.paste._verify_direct_text_input(False, "", "hello", False)')
    v = get_result(r)
    if p(r.get("ok", False) and v == [True, "unreadable"],
         "Unreadable before → immediate return", str(v)):
        passed += 1

    # 5b. 文字未變 → 第 2 次 backoff 就 early exit (False, 'unchanged')
    total += 1
    # 先讀實際焦點文字，確保 before==after
    snap = get_result(eval_expr("self.paste._focused_text_snapshot()"))
    if isinstance(snap, list) and snap[0]:
        safe_before = snap[1].replace("\\", "\\\\").replace('"', '\\"')
        r = eval_expr(
            f'self.paste._verify_direct_text_input(True, "{safe_before}", '
            f'"ZZZZZ_impossible_text", False)'
        )
        v = get_result(r)
        if p(r.get("ok", False) and v == [False, "unchanged"],
             "Unchanged text → early exit", str(v)):
            passed += 1
    else:
        # UIA 不可讀時測 unreadable 路徑
        r = eval_expr('self.paste._verify_direct_text_input(True, "x", "y", False)')
        v = get_result(r)
        if p(r.get("ok", False) and isinstance(v, list) and v[0],
             "UIA unreadable fallback", str(v)):
            passed += 1

    # 5c. empty final_text (suffix_len=0 邊界)
    total += 1
    r = eval_expr('self.paste._verify_direct_text_input(True, "zzzz_unique_empty", "", False)')
    v = get_result(r)
    if p(r.get("ok", False) and isinstance(v, list) and len(v) == 2,
         "Empty final_text no crash", str(v)):
        passed += 1

    # 5d. BACKOFF 常數正確
    total += 1
    r = eval_expr(
        "list(__import__('ai_whisper.services.paste_service', "
        "fromlist=['UNICODE_INPUT_VERIFY_BACKOFF_SEC'])"
        ".UNICODE_INPUT_VERIFY_BACKOFF_SEC)"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v == [0.06, 0.12, 0.24],
         "BACKOFF_SEC constant", str(v)):
        passed += 1

    # 5e. 舊常數已移除
    total += 1
    r = eval_expr(
        "not hasattr(__import__('ai_whisper.services.paste_service', fromlist=['x']), "
        "'UNICODE_INPUT_VERIFY_POLL_SEC')"
    )
    if p(r.get("ok", False) and get_result(r) is True,
         "Old POLL_SEC removed", str(get_result(r))):
        passed += 1

    # ── 6. Timer debounce 檢查 ──
    print("\n-- 6. Timer debounce references exist --")

    # 6a. InputService 有 debounce Timer 引用
    total += 1
    r = eval_expr(
        "hasattr(self.input, '_hotkey_cleanup_timer')"
    )
    if p(r.get("ok", False) and get_result(r) is True,
         "InputService has Timer debounce attrs"):
        passed += 1

    # 6b. Timer 引用初始為 None（閒置時沒有殘留 Timer）
    total += 1
    r = eval_expr(
        "self.input._hotkey_cleanup_timer is None "
        "or not self.input._hotkey_cleanup_timer.is_alive()"
    )
    if p(r.get("ok", False) and get_result(r) is True,
         "Hotkey cleanup timer idle (None or not alive)"):
        passed += 1

    # ── 7. H6: UIA timeout 保護 ──
    print("\n-- 7. H6: UIA timeout protection --")

    total += 1
    r = eval_expr(
        "__import__('ai_whisper.services.paste_service', "
        "fromlist=['UIA_TIMEOUT_SEC']).UIA_TIMEOUT_SEC"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v == 2.0,
         "UIA_TIMEOUT_SEC constant", str(v)):
        passed += 1

    # ── 8. L7: ThreadPoolExecutor workers ──
    print("\n-- 8. L7: ThreadPoolExecutor workers --")

    total += 1
    r = eval_expr("self.executor._max_workers")
    v = get_result(r)
    if p(r.get("ok", False) and v == 4,
         "ThreadPoolExecutor max_workers=4", str(v)):
        passed += 1

    # ── 9. L8: PasteService.shutdown 方法存在 ──
    print("\n-- 9. L8: PasteService shutdown method --")

    total += 1
    r = eval_expr("callable(getattr(self.paste, 'shutdown', None))")
    if p(r.get("ok", False) and get_result(r) is True,
         "PasteService has shutdown()"):
        passed += 1

    # ── 10. SendInput UNICODE 直接輸入路徑 ──
    print("\n-- 10. SendInput UNICODE direct text input --")

    # 10a. send_unicode_text 批次送出基本功能
    total += 1
    r = eval_expr("self.input.send_unicode_text('')")
    v = get_result(r)
    if p(r.get("ok", False) and v is True,
         "send_unicode_text('') returns True", str(v)):
        passed += 1

    # 10b. _should_use_direct_text_input 對一般應用預設 True（黑名單制）
    total += 1
    r = eval_expr(
        "self.paste._should_use_direct_text_input("
        "'Notepad', 'notepad.exe', False, ('', '', '', ''))"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v is True,
         "Default direct text input = True (blacklist mode)", str(v)):
        passed += 1

    # 10c. preserve_ctrl_modifier=True 時仍走剪貼簿
    total += 1
    r = eval_expr(
        "self.paste._should_use_direct_text_input("
        "'Notepad', 'notepad.exe', True, ('', '', '', ''))"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v is False,
         "preserve_ctrl_modifier=True → False", str(v)):
        passed += 1

    # 10d. DIRECT_TEXT_READABLE_MAX_CHARS 已提高到 500
    total += 1
    r = eval_expr(
        "__import__('ai_whisper.services.paste_service', "
        "fromlist=['DIRECT_TEXT_READABLE_MAX_CHARS'])"
        ".DIRECT_TEXT_READABLE_MAX_CHARS"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v == 500,
         "DIRECT_TEXT_READABLE_MAX_CHARS=500", str(v)):
        passed += 1

    # 10e. 黑名單是 frozenset
    total += 1
    r = eval_expr(
        "type(self.paste._DIRECT_TEXT_BLACKLIST).__name__"
    )
    v = get_result(r)
    if p(r.get("ok", False) and v == "frozenset",
         "Blacklist is frozenset", str(v)):
        passed += 1

    # ── 結果 ──
    summary = f"Result: {passed}/{total} passed"
    log_to_app(summary)
    log_to_app("Paste Test END " + ("ALL PASSED" if passed == total else f"{total - passed} FAILED"))
    log_to_app("=" * 40)

    print("\n" + "=" * 60)
    print(summary)
    if passed == total:
        print("[v] ALL PASSED")
    else:
        print(f"[!] {total - passed} FAILED")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
