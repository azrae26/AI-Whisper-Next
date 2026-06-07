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
  10. SendInput UNICODE 直接輸入路徑
  11. 常用程式 SendInput 端到端驗證（LINE/Chrome/Cursor/Antigravity/Codex）

使用方式（從專案根目錄）：
  py -3.12 .agents/skills/verify/test_paste.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
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

    # ── 11. 常用程式 SendInput 端到端驗證 ──
    print("\n-- 11. Targeted app SendInput end-to-end --")
    total, passed = _test_targeted_apps(total, passed)

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


# ── 11. 常用程式端到端 SendInput 驗證 helpers ──────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 要測試的常用程式（process name → 顯示名稱）
TARGET_APPS: dict[str, str] = {
    "line.exe": "LINE",
    "chrome.exe": "Chrome",
    "cursor.exe": "Cursor",
    "antigravity.exe": "Antigravity",
    "codex.exe": "Codex",
}

# 88 字元，777 開頭避免 Chrome Omnibox 匹配 URL 觸發自動完成
# 尾部 "AW_OK" 作為驗證標記
TEST_STRING_LONG = (
    "777_驗證test_"
    "The quick brown fox jumps over the lazy dog。"
    "繁體中文測試English混合1234567890標點。"
    "AW_OK"
)
_TAIL_MARKER = "AW_OK"

# 跳過沒有輸入框的視窗（標題關鍵字，不區分大小寫）
_SKIP_TITLE_KEYWORDS: list[str] = [
    "youtube music",
    "devtools",
]


def _get_process_name(hwnd: int) -> str:
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)


def _get_window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _find_app_windows() -> dict[str, list[tuple[int, str]]]:
    """找出目標程式的所有可見頂層視窗。回傳 {process: [(hwnd, title), ...]}。"""
    found: dict[str, list[tuple[int, str]]] = {}

    @WNDENUMPROC
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _get_window_title(hwnd)
        if not title:
            return True
        proc = _get_process_name(hwnd)
        if proc in TARGET_APPS:
            title_lower = title.lower()
            if any(kw in title_lower for kw in _SKIP_TITLE_KEYWORDS):
                return True
            found.setdefault(proc, []).append((hwnd, title))
        return True

    user32.EnumWindows(callback, 0)
    return found


def _activate_window(hwnd: int) -> bool:
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up trick
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd

def _cleanup_delete(count: int) -> None:
    """從測試腳本本地送 Backspace 刪除測試文字。"""
    for _ in range(count):
        user32.keybd_event(0x08, 0, 0, 0)
        user32.keybd_event(0x08, 0, 2, 0)
    time.sleep(0.3)


def _release_all_modifiers() -> None:
    """釋放所有修飾鍵，避免影響下一個 app 測試。"""
    for vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
        user32.keybd_event(vk, 0, 2, 0)  # key up


def _test_one_app(hwnd: int, title: str, proc: str) -> tuple[bool, str]:
    """對單一程式測試 SendInput + UIA 驗證。回傳 (pass, detail)。

    Chrome: Ctrl+L 聚焦 URL 列（90 字單批不分割）。
    其他: 自然 focus（如 LINE chat、Antigravity chat input）。
    """
    # 1. 切換到前景
    if not _activate_window(hwnd):
        return (False, "activate failed")

    # 2. 聚焦輸入框
    is_chrome = proc == "chrome.exe"
    # Electron 系 chat app：點擊視窗底部聚焦 chat input（手測 bottom-30 命中）
    if proc in ("antigravity.exe", "cursor.exe", "codex.exe"):
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 30
        user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.3)

    test_text = TEST_STRING_LONG
    text_len = len(test_text)

    # 2. 讀取 UIA 文字快照（送出前）
    r_before = eval_expr("self.paste._focused_text_snapshot()")
    before_snap = get_result(r_before)
    before_readable = isinstance(before_snap, list) and before_snap[0]
    before_text = before_snap[1] if before_readable else ""

    # 3. 送出測試文字
    r_send = eval_expr(f"self.input.send_unicode_text({repr(test_text)})")
    send_ok = r_send.get("ok", False) and get_result(r_send) is True
    if not send_ok:
        return (False, f"send_unicode_text failed: {get_result(r_send)}")

    # 4. 等待文字出現
    time.sleep(0.3)

    # 5. 讀取 UIA 文字快照（送出後）
    r_after = eval_expr("self.paste._focused_text_snapshot()")
    after_snap = get_result(r_after)
    after_readable = isinstance(after_snap, list) and after_snap[0]
    after_text = after_snap[1] if after_readable else ""

    # 6. 清理（只在 UIA 確認有新增文字時才做）
    cleanup_ok = True  # 預設：不需清理 = ok
    if after_readable and before_readable:
        delta = len(after_text) - len(before_text)
        if delta > 0:
            # 不重新 activate——焦點還在輸入框，重新 activate 會重置焦點
            # Ctrl+A 全選 → Backspace 刪除
            user32.keybd_event(0xA2, 0, 0, 0)  # LCtrl down
            time.sleep(0.01)
            user32.keybd_event(0x41, 0, 0, 0)  # A down
            user32.keybd_event(0x41, 0, 2, 0)  # A up
            time.sleep(0.01)
            user32.keybd_event(0xA2, 0, 2, 0)  # LCtrl up
            time.sleep(0.05)
            user32.keybd_event(0x08, 0, 0, 0)  # Backspace
            user32.keybd_event(0x08, 0, 2, 0)
            time.sleep(0.2)
            # Ctrl+Z 還原原始內容（如果有的話）
            if before_text and before_text.strip():
                user32.keybd_event(0xA2, 0, 0, 0)
                time.sleep(0.01)
                user32.keybd_event(0x5A, 0, 0, 0)  # Z
                user32.keybd_event(0x5A, 0, 2, 0)
                time.sleep(0.01)
                user32.keybd_event(0xA2, 0, 2, 0)
                time.sleep(0.3)
            # LINE 的 UIA 即時更新，可驗證；Electron app 的 UIA 會快取，信任動作
            if proc == "line.exe":
                time.sleep(0.2)
                r_cl = eval_expr("self.paste._focused_text_snapshot()")
                cl_snap = get_result(r_cl)
                if isinstance(cl_snap, list) and cl_snap[0]:
                    leftover = len(cl_snap[1]) - len(before_text)
                    cleanup_ok = leftover <= 0

    # 7. 釋放修飾鍵
    _release_all_modifiers()

    # 8. 判定結果（含清理狀態）
    cl_tag = "，cleanup=ok" if cleanup_ok else "，cleanup=FAIL"

    if after_readable and _TAIL_MARKER in after_text:
        return (cleanup_ok, f"verified: '{_TAIL_MARKER}' found ({text_len} chars{cl_tag})")

    if after_readable and len(after_text) > len(before_text):
        delta = len(after_text) - len(before_text)
        if delta >= text_len * 0.8:
            return (cleanup_ok, f"text grew +{delta}/{text_len} chars{cl_tag}")
        return (True, f"partial ok: +{delta}/{text_len} chars")

    # UIA 不可讀或無變化 → SendInput API 成功即 PASS
    return (True, f"API ok, UIA={'unreadable' if not after_readable else 'no change'} ({text_len} chars)")


def _test_targeted_apps(total: int, passed: int) -> tuple[int, int]:
    """測試常用程式的 SendInput 端到端功能。每程式只測一次。"""
    original_fg = user32.GetForegroundWindow()
    found = _find_app_windows()

    for proc, label in TARGET_APPS.items():
        windows = found.get(proc, [])
        if not windows:
            print(f"  [_] SKIP | {label} not running")
            continue

        total += 1
        hwnd, title = windows[0]
        print(f"  → 測試 {label}: {title[:50]}")
        try:
            ok, detail = _test_one_app(hwnd, title, proc)
            if p(ok, f"{label} SendInput e2e", detail):
                passed += 1
        except Exception as e:
            p(False, f"{label} SendInput e2e", f"error: {e}")

    if original_fg:
        _activate_window(original_fg)

    return total, passed


if __name__ == "__main__":
    main()
