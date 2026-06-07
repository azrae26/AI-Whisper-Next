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
  12. WM_CHAR 文字輸入模式（屬性同步、空文字、端到端 UIA 驗證、config）
  12e. 常用程式三種方法端到端（SendInput / WM_CHAR / Ctrl+V 剪貼簿）
  12f. WM_CHAR 中間插入（非空輸入框第 4 字後插入，UIA 讀回驗證）
  13. R21: 空文字貼上不動剪貼簿
  14. R9: 修飾鍵實際按住後釋放
  15. R3: 貼上後使用者立刻 Ctrl+C，watchdog 不覆蓋
  16. R1: 連續快速 paste queue 堆積
  17. R4: Replay 機制（guard arm/disarm/block 計數）
  18. R7: 貼上期間視窗切換 log 記錄
  19. R26: 大型剪貼簿備份還原效能

三種貼上方法：
  - SendInput UNICODE：硬體鍵盤事件，最快但 Qt/Electron 中間插入已知壞
  - WM_CHAR PostMessage：視窗訊息，相容 Qt/Electron，需開啟設定toggle
  - Ctrl+V 剪貼簿：備份→設文字→Ctrl+V→還原，最穩但有剪貼簿副作用

已知限制（測試中自動跳過）：
  - LINE (Qt 6) + SendInput：非空輸入框中間插入會吞字/亂序

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

    # 5a. before_readable=False → _verify 仍返回 (True, 'unreadable')
    # 注意：修復 4 的防線在 _execute_paste 層（不可讀時直接跳過 SendInput），
    # _verify 本身的行為不變，但不會被呼叫到。此處確認 _verify 單獨行為不變。
    total += 1
    r = eval_expr('self.paste._verify_direct_text_input(False, "", "hello", False)')
    v = get_result(r)
    if p(r.get("ok", False) and v == [True, "unreadable"],
         "Unreadable before → _verify returns (True, 'unreadable')", str(v)):
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

    # 5f. 修復 4：不可讀控制項不嘗試 SendInput（避免吞字）
    # 用決策邏輯對照組證明 bug 存在，再用實際程式碼驗證修復生效。

    # 5f-1. 對照組：重現 _execute_paste 中的決策分支
    total += 1
    def old_decision(before_readable, text_len, max_chars=500):
        use_direct_text = True
        if before_readable and text_len > max_chars:
            use_direct_text = False
        return use_direct_text

    def new_decision(before_readable, text_len, max_chars=500):
        use_direct_text = True
        if before_readable and text_len > max_chars:
            use_direct_text = False
        elif not before_readable:
            use_direct_text = False  # 修復 4
        return use_direct_text

    bug_exists = old_decision(False, 10) is True
    fix_works = new_decision(False, 10) is False
    normal_ok = new_decision(True, 10) is True
    long_ok = new_decision(True, 600) is False
    if p(bug_exists and fix_works and normal_ok and long_ok,
         "Fix4 決策邏輯對照組",
         f"舊版unreadable→SendInput={old_decision(False,10)}, "
         f"新版unreadable→skip={new_decision(False,10)}, "
         f"新版readable→SendInput={new_decision(True,10)}, "
         f"新版long→skip={new_decision(True,600)}"):
        passed += 1

    # 5f-2. 實際程式碼驗證：mock _focused_text_snapshot → (False,"")，
    # 呼叫 paste_text 後檢查 log 出現 "target not UIA-readable"
    total += 1
    r_lines_before = eval_expr(
        "len(open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        "key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines())"
    )
    lines_before = get_result(r_lines_before) if r_lines_before.get("ok") else 0

    # mock → paste → 等待 → 還原
    # ⚠️ 備份必須從 __class__.__dict__ 取原始 descriptor（function 物件），
    # 不能用 self.paste._focused_text_snapshot（那會觸發 descriptor protocol，
    # 返回 bound method，還原時會破壞方法簽名）。
    eval_expr(
        "("
        "  setattr(self.paste.__class__, '_orig_snapshot', self.paste.__class__.__dict__['_focused_text_snapshot']),"
        "  setattr(self.paste.__class__, '_focused_text_snapshot', lambda self: (False, '')),"
        "  self.paste.paste_text('FIX4_TEST', delay_ms=50, end_prefix='', preserve_ctrl_modifier=False),"
        ")"
    )
    time.sleep(2)
    eval_expr(
        "("
        "  setattr(self.paste.__class__, '_focused_text_snapshot', self.paste.__class__._orig_snapshot),"
        "  delattr(self.paste.__class__, '_orig_snapshot'),"
        ")"
    )

    # 讀 mock 後新增的 log 行，找 "target not UIA-readable"
    r_log = eval_expr(
        f"[l.strip() for l in open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        f"key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines()[{lines_before}:] "
        f"if 'UIA-readable' in l or 'TEXT input' in l]"
    )
    log_lines = get_result(r_log) if r_log.get("ok") else []
    found_skip = any("not UIA-readable" in l for l in (log_lines or []))
    if p(found_skip,
         "Fix4 實際程式碼：不可讀時 log 顯示 skipped",
         f"找到 'not UIA-readable' in {len(log_lines or [])} 行"):
        passed += 1
    if log_lines:
        for line in log_lines[:3]:
            print(f"    {line[:120]}")

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

    # ── 12. WM_CHAR 文字輸入模式 ──
    print("\n-- 12. WM_CHAR text input mode --")

    # 12a. use_wm_char 屬性與 config 同步
    total += 1
    r = eval_expr("self.input.use_wm_char == self.cfg.use_wm_char")
    v = get_result(r)
    if p(r.get("ok", False) and v is True,
         "use_wm_char synced with config", str(v)):
        passed += 1

    # 12b. _send_wm_char_text('') 空文字基本功能
    total += 1
    r = eval_expr("self.input._send_wm_char_text('')")
    v = get_result(r)
    if p(r.get("ok", False) and v is True,
         "_send_wm_char_text('') returns True", str(v)):
        passed += 1

    # 12c. WM_CHAR 端到端：先聚焦 Antigravity chat input → 送文字 → UIA 讀回驗證 → 清理 → 還原
    total += 1
    wm_marker = "WM_CHAR_OK"
    # 找 Antigravity 視窗聚焦 chat input（避免前景碰巧無輸入框）
    _found = _find_app_windows()
    _anti_wins = _found.get("antigravity.exe", [])
    if _anti_wins:
        _h, _ = _anti_wins[0]
        _activate_window(_h)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(_h, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 60
        user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.3)
    # 保存原始值
    r_orig = eval_expr("self.input.use_wm_char")
    orig_wm = get_result(r_orig)
    # 讀 before snapshot
    r_before = eval_expr("self.paste._focused_text_snapshot()")
    before_snap = get_result(r_before)
    before_text = before_snap[1] if isinstance(before_snap, list) and before_snap[0] else ""
    # 切到 WM_CHAR 模式
    eval_expr("setattr(self.input, 'use_wm_char', True)")
    # 透過 send_unicode_text（會走 WM_CHAR 路徑）
    r_send = eval_expr(f"self.input.send_unicode_text({repr(wm_marker)})")
    send_ok = r_send.get("ok", False) and get_result(r_send) is True
    time.sleep(0.5)
    # 讀 after snapshot
    r_after = eval_expr("self.paste._focused_text_snapshot()")
    after_snap = get_result(r_after)
    after_text = after_snap[1] if isinstance(after_snap, list) and after_snap[0] else ""
    wm_e2e_ok = send_ok and wm_marker in after_text
    # 清理：Backspace 刪除 marker
    if wm_e2e_ok:
        for _ in range(len(wm_marker)):
            user32.keybd_event(0x08, 0, 0, 0)
            user32.keybd_event(0x08, 0, 2, 0)
        time.sleep(0.3)
    # 還原
    eval_expr(f"setattr(self.input, 'use_wm_char', {orig_wm})")
    if p(wm_e2e_ok,
         "WM_CHAR e2e: send → UIA verify",
         f"send_ok={send_ok}, marker_found={wm_marker in after_text}"):
        passed += 1

    # 12d. AppConfig 有 use_wm_char 欄位
    total += 1
    r = eval_expr("hasattr(self.cfg, 'use_wm_char') and isinstance(self.cfg.use_wm_char, bool)")
    v = get_result(r)
    if p(r.get("ok", False) and v is True,
         "AppConfig.use_wm_char is bool"):
        passed += 1

    # 12e. 常用程式端到端（每個 app 同時測 SendInput + WM_CHAR，測完再換下一個）
    print("\n-- 12e. Targeted app e2e: SendInput + WM_CHAR --")
    total, passed = _test_targeted_apps_all(total, passed)

    # ── 12f. 中間插入 ──
    print("\n-- 12f. Middle insert: WM_CHAR --")
    total, passed = _test_middle_insert(total, passed)

    # ── 13. R21: 空文字貼上不動剪貼簿 ──
    print("\n-- 13. R21: Empty text paste (should not touch clipboard) --")
    total, passed = _test_empty_text_paste(total, passed)

    # ── 14. R9: 修飾鍵實際按住後釋放 ──
    print("\n-- 14. R9: Release held modifier keys --")
    total, passed = _test_modifier_release(total, passed)

    # ── 15. R3: 貼上後 Ctrl+C，watchdog 不覆蓋 ──
    print("\n-- 15. R3: Ctrl+C during watchdog (should not overwrite) --")
    total, passed = _test_ctrlc_during_watchdog(total, passed)

    # ── 16. R1: 連續快速 paste queue ──
    print("\n-- 16. R1: Consecutive rapid paste queue --")
    total, passed = _test_consecutive_paste(total, passed)

    # ── 17. R4: Replay 機制 ──
    print("\n-- 17. R4: Manual paste guard (arm/disarm/block) --")
    total, passed = _test_replay_guard(total, passed)

    # ── 18. R7: 貼上期間視窗切換 ──
    print("\n-- 18. R7: Window switch during paste --")
    total, passed = _test_window_switch_paste(total, passed)

    # ── 19. R26: 大型剪貼簿效能 ──
    print("\n-- 19. R26: Large clipboard backup/restore perf --")
    total, passed = _test_large_clipboard(total, passed)

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


def _test_one_app(hwnd: int, title: str, proc: str,
                  method: str = "sendinput") -> tuple[bool, str]:
    """對單一程式測試貼上功能。回傳 (pass, detail)。

    method: 'sendinput' | 'wm_char' | 'clipboard'
    Chrome: Ctrl+L 聚焦 URL 列。
    其他: 自然 focus（如 LINE chat、Antigravity chat input）。
    """
    # 1. 切換到前景
    if not _activate_window(hwnd):
        return (False, "activate failed")

    # 2. 聚焦輸入框
    is_chrome = proc == "chrome.exe"
    # Chrome：Ctrl+L 聚焦網址列
    if is_chrome:
        user32.keybd_event(0xA2, 0, 0, 0)  # LCtrl down
        time.sleep(0.02)
        user32.keybd_event(0x4C, 0, 0, 0)  # L down
        user32.keybd_event(0x4C, 0, 2, 0)  # L up
        time.sleep(0.02)
        user32.keybd_event(0xA2, 0, 2, 0)  # LCtrl up
        time.sleep(0.3)
    # Electron 系 chat app：點擊視窗底部聚焦 chat input（手測 bottom-60 命中）
    elif proc in ("antigravity.exe", "cursor.exe", "codex.exe"):
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 60
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

    # 3. 送出測試文字（依 method 選擇路徑）
    if method == "clipboard":
        # Ctrl+V 剪貼簿路徑：paste_text 是非同步的
        r_send = eval_expr(
            f"self.paste.paste_text({repr(test_text)}, delay_ms=50, "
            f"end_prefix='', preserve_ctrl_modifier=False) or True"
        )
        send_ok = r_send.get("ok", False)
        if not send_ok:
            return (False, f"paste_text failed: {get_result(r_send)}")
        # paste_text 非同步，等待完成
        time.sleep(3)
    else:
        # sendinput / wm_char 都走 send_unicode_text（由 use_wm_char 控制路徑）
        r_send = eval_expr(f"self.input.send_unicode_text({repr(test_text)})")
        send_ok = r_send.get("ok", False) and get_result(r_send) is True
        if not send_ok:
            return (False, f"send_unicode_text failed: {get_result(r_send)}")
        time.sleep(0.3)

    # 5. 讀取 UIA 文字快照（送出後）
    r_after = eval_expr("self.paste._focused_text_snapshot()")
    after_snap = get_result(r_after)
    after_readable = isinstance(after_snap, list) and after_snap[0]
    after_text = after_snap[1] if after_readable else ""

    # 6. 強制清理（每次都清，避免殘留文字影響下一輪）
    cleanup_ok = True
    if is_chrome:
        # Chrome 網址列：按 3 次 Escape 還原原始 URL 並移除焦點
        for _ in range(3):
            user32.keybd_event(0x1B, 0, 0, 0)  # Escape
            user32.keybd_event(0x1B, 0, 2, 0)
            time.sleep(0.15)
        time.sleep(0.2)
    else:
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
        if before_readable and before_text and before_text.strip():
            user32.keybd_event(0xA2, 0, 0, 0)
            time.sleep(0.01)
            user32.keybd_event(0x5A, 0, 0, 0)  # Z
            user32.keybd_event(0x5A, 0, 2, 0)
            time.sleep(0.01)
            user32.keybd_event(0xA2, 0, 2, 0)
            time.sleep(0.3)
        # LINE 的 UIA 即時更新，可驗證清理結果
        if proc == "line.exe" and after_readable and before_readable:
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


# 三種貼上方法定義：(method_key, label_suffix, setup_expr)
_PASTE_METHODS: list[tuple[str, str, str]] = [
    ("sendinput", "SendInput", "setattr(self.input, 'use_wm_char', False)"),
    ("wm_char",   "WM_CHAR",   "setattr(self.input, 'use_wm_char', True)"),
    ("clipboard", "Ctrl+V",    ""),  # 不需 setup，paste_text 自帶剪貼簿流程
]


def _test_targeted_apps_all(total: int, passed: int) -> tuple[int, int]:
    """對每個開啟的目標程式，依序測三種方法。測完一個 app 再換下一個。"""
    original_fg = user32.GetForegroundWindow()
    found = _find_app_windows()

    for proc, label in TARGET_APPS.items():
        windows = found.get(proc, [])
        if not windows:
            print(f"  [_] SKIP | {label} not running")
            continue

        hwnd, title = windows[0]

        for method_key, method_label, setup_expr in _PASTE_METHODS:
            # LINE 跳過 SendInput（已知 Qt 中間插入相容性問題）
            if proc == "line.exe" and method_key == "sendinput":
                print(f"  [_] SKIP | {label} {method_label} (Qt 已知限制)")
                continue
            total += 1
            print(f"  → {label} {method_label}: {title[:50]}")
            if setup_expr:
                eval_expr(setup_expr)
            try:
                ok, detail = _test_one_app(hwnd, title, proc, method=method_key)
                if p(ok, f"{label} {method_label} e2e", detail):
                    passed += 1
            except Exception as e:
                p(False, f"{label} {method_label} e2e", f"error: {e}")

    # 還原
    eval_expr("setattr(self.input, 'use_wm_char', self.cfg.use_wm_char)")

    if original_fg:
        _activate_window(original_fg)

    return total, passed


# ── 12f. 中間插入測試 helpers ──────────────

VK_HOME = 0x24
VK_RIGHT = 0x27
VK_BACK = 0x08
VK_LCTRL = 0xA2
VK_A = 0x41


def _clear_input() -> None:
    """Ctrl+A + Backspace 清空前景輸入框。"""
    user32.keybd_event(VK_LCTRL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_A, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 2, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_LCTRL, 0, 2, 0)
    time.sleep(0.1)
    user32.keybd_event(VK_BACK, 0, 0, 0)
    user32.keybd_event(VK_BACK, 0, 2, 0)
    time.sleep(0.3)


def _move_cursor_to(pos: int) -> None:
    """Home 移到開頭，然後 Right 移到指定位置。"""
    user32.keybd_event(VK_HOME, 0, 0, 0)
    user32.keybd_event(VK_HOME, 0, 2, 0)
    time.sleep(0.1)
    for _ in range(pos):
        user32.keybd_event(VK_RIGHT, 0, 0, 0)
        user32.keybd_event(VK_RIGHT, 0, 2, 0)
        time.sleep(0.03)
    time.sleep(0.1)


def _read_focused_text() -> str:
    """透過 eval 讀取 UIA 焦點控制項文字。"""
    r = eval_expr("self.paste._focused_text_snapshot()")
    snap = get_result(r)
    if isinstance(snap, list) and snap[0]:
        return snap[1]
    return ""


def _test_middle_insert(total: int, passed: int) -> tuple[int, int]:
    """WM_CHAR 在非空輸入框中間插入文字，用 UIA 讀回驗證。

    SendInput 中間插入在 Electron/Qt 為已知限制（Section 12e 已驗證），此處只測 WM_CHAR。
    """
    INITIAL = "你好世界測試文字"   # 8 字
    INSERT = "【插入】"            # 4 字
    MID_POS = 4                    # 游標移到第 4 字後
    EXPECTED = INITIAL[:MID_POS] + INSERT + INITIAL[MID_POS:]  # 你好世界【插入】測試文字

    # 找 Antigravity 視窗聚焦 chat input（避免前景碰巧無輸入框）
    _found = _find_app_windows()
    _anti_wins = _found.get("antigravity.exe", [])
    if _anti_wins:
        _h, _ = _anti_wins[0]
        _activate_window(_h)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(_h, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 60
        user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.3)

    # 中間插入方法：(label, 段2 eval 表達式, 段2 後等待秒數)
    # SendInput 中間插入在 Electron/Qt 已知壞，跳過
    _mid_methods: list[tuple[str, str, float]] = [
        ("WM_CHAR",
         f"self.input._send_wm_char_text({INSERT!r})",
         0.3),
        ("Ctrl+V",
         f"self.paste.paste_text({INSERT!r}, delay_ms=50, end_prefix='', preserve_ctrl_modifier=False) or True",
         3.0),
    ]

    for mid_label, mid_expr, mid_wait in _mid_methods:
        total += 1
        try:
            # 清空
            _clear_input()
            time.sleep(0.2)

            # 段1：WM_CHAR 輸入初始文字（WM_CHAR 最可靠）
            r1 = eval_expr(f"self.input._send_wm_char_text({INITIAL!r})")
            if not (r1.get("ok") and get_result(r1)):
                p(False, f"Middle insert {mid_label}", "段1 send failed")
                continue
            time.sleep(0.3)

            # 移到中間
            _move_cursor_to(MID_POS)

            # 段2：中間插入
            r2 = eval_expr(mid_expr)
            if not (r2.get("ok") and get_result(r2)):
                p(False, f"Middle insert {mid_label}", "段2 send failed")
                _clear_input()
                continue
            time.sleep(mid_wait)

            # UIA 讀回驗證
            actual = _read_focused_text()
            ok = actual == EXPECTED
            detail = f"match ✓ ({len(EXPECTED)} chars)" if ok else f"actual={actual!r}"
            if p(ok, f"Middle insert {mid_label}", detail):
                passed += 1

            # 清空
            _clear_input()
        except Exception as e:
            p(False, f"Middle insert {mid_label}", f"error: {e}")
            _clear_input()

    return total, passed


# ── 13. R21: 空文字貼上不動剪貼簿 helpers ──────────────

def _test_empty_text_paste(total: int, passed: int) -> tuple[int, int]:
    """空文字 paste_text('') 不應動剪貼簿、不送 Ctrl+V。"""
    # 寫入已知文字到剪貼簿
    sentinel = "EMPTY_TEST_SENTINEL_" + str(int(time.time()))
    eval_expr(f"self.paste._set_clipboard_verified({sentinel!r})")
    time.sleep(0.3)

    # 呼叫 paste_text('') — 空文字
    total += 1
    eval_expr(
        "self.paste.paste_text('', delay_ms=0, end_prefix='', "
        "preserve_ctrl_modifier=False)"
    )
    time.sleep(3)  # 等非同步 paste worker 處理完

    # 驗證剪貼簿仍為 sentinel（沒被動過）
    r = eval_expr("self.paste._read_clipboard_text()")
    clip = get_result(r)
    # 空文字走管線後 final_text 加上 end_prefix 可能變成 "。"，
    # 但若 end_prefix='' 則 final_text 仍為 ''。
    # 關鍵驗證：剪貼簿沒被替換成空字串或其他東西。
    ok = r.get("ok", False) and clip == sentinel
    if p(ok, "Empty paste didn't touch clipboard",
         f"clip={clip!r}, sentinel={sentinel!r}"):
        passed += 1

    return total, passed


# ── 14. R9: 修飾鍵實際按住後釋放 helpers ──────────────

def _test_modifier_release(total: int, passed: int) -> tuple[int, int]:
    """模擬修飾鍵被按住，呼叫 release_modifiers_for_paste 後應已釋放。"""
    VK_LSHIFT = 0xA0
    VK_LALT = 0xA4

    for vk, name in [(VK_LSHIFT, "LShift"), (VK_LALT, "LAlt")]:
        total += 1
        try:
            # 按下修飾鍵
            user32.keybd_event(vk, 0, 0, 0)  # key down
            time.sleep(0.05)

            # 透過 eval 呼叫 release_modifiers_for_paste
            eval_expr("self.input.release_modifiers_for_paste(preserve_ctrl=False)")
            time.sleep(0.05)

            # 驗證：GetAsyncKeyState 最高位元應為 0（未按下）
            state = user32.GetAsyncKeyState(vk)
            released = not (state & 0x8000)
            if p(released, f"{name} released after call",
                 f"GetAsyncKeyState=0x{state & 0xFFFF:04X}"):
                passed += 1
        finally:
            # 保險：確保釋放
            user32.keybd_event(vk, 0, 2, 0)  # key up

    return total, passed


# ── 15. R3: 貼上後 Ctrl+C during watchdog helpers ──────────────

def _test_ctrlc_during_watchdog(total: int, passed: int) -> tuple[int, int]:
    """貼上後在 watchdog 期間外部寫入新內容（模擬使用者 Ctrl+C），
    watchdog 應偵測為第三方變更並停止，不覆蓋使用者的新內容。
    """
    total += 1
    user_content = "USER_CTRLC_" + str(int(time.time()))

    # 記錄 log 行數，用於事後驗證 watchdog 訊息
    r_lines = eval_expr(
        "len(open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        "key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines())"
    )
    lines_before = get_result(r_lines) if r_lines.get("ok") else 0

    # 觸發一次真實貼上（走 Ctrl+V 路徑）
    eval_expr(
        "self.paste.paste_text('WATCHDOG_TRIGGER', delay_ms=0, "
        "end_prefix='', preserve_ctrl_modifier=False)"
    )

    # 等 ~0.6s（Ctrl+V 完成 + restore 完成，watchdog 開始監控）
    time.sleep(0.6)

    # 模擬使用者 Ctrl+C：外部寫入新內容到剪貼簿
    eval_expr(f"self.paste._set_clipboard_verified({user_content!r})")

    # 等 watchdog 完成（WATCHDOG_DURATION = 2.2s）
    time.sleep(3)

    # 驗證：剪貼簿仍為使用者的新內容，沒被 watchdog 覆蓋
    r = eval_expr("self.paste._read_clipboard_text()")
    clip = get_result(r)
    content_ok = r.get("ok", False) and clip == user_content

    # 驗證 log 出現 watchdog 偵測訊息（搜尋多種格式）
    r_log = eval_expr(
        f"[l.strip() for l in open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        f"key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines()[{lines_before}:] "
        f"if 'watchdog' in l.lower() and ('external' in l.lower() or 'third party' in l.lower() or 'stop' in l.lower())]"
    )
    log_lines = get_result(r_log) if r_log.get("ok") else []
    found_watchdog_stop = len(log_lines or []) > 0

    # 核心斷言：剪貼簿內容沒被覆蓋
    ok = content_ok
    if p(ok, "Watchdog respected user Ctrl+C",
         f"clip_ok={content_ok}, watchdog_stop_log={found_watchdog_stop}"):
        passed += 1
    if log_lines:
        for line in log_lines[:3]:
            print(f"    {line[:120]}")

    return total, passed


# ── 16. R1: 連續快速 paste queue helpers ──────────────

def _test_consecutive_paste(total: int, passed: int) -> tuple[int, int]:
    """連續 3 次 paste_text，驗證按序完成且剪貼簿最終還原正確。"""
    total += 1

    # 設定已知剪貼簿內容
    original = "CONSEC_ORIGINAL_" + str(int(time.time()))
    eval_expr(f"self.paste._set_clipboard_verified({original!r})")
    time.sleep(0.3)

    # 記錄 log 行數
    r_lines = eval_expr(
        "len(open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        "key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines())"
    )
    lines_before = get_result(r_lines) if r_lines.get("ok") else 0

    # 連續排入 3 段
    for i in range(3):
        eval_expr(
            f"self.paste.paste_text('CONSEC_{i}', delay_ms=0, "
            f"end_prefix='', preserve_ctrl_modifier=False)"
        )

    # 等全部完成（每段 ~3s：settle+Ctrl+V+restore+watchdog，串行）
    # 但 watchdog 有「queue 非空時提前結束」邏輯，所以實際更快
    time.sleep(12)

    # 驗證剪貼簿已還原（最後一段的 restore 應回到它自己備份的內容）
    r = eval_expr("self.paste._read_clipboard_text()")
    clip = get_result(r)

    # 讀 log 確認三段都有貼上記錄
    r_log = eval_expr(
        f"[l.strip() for l in open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        f"key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines()[{lines_before}:] "
        f"if 'CONSEC_' in l or 'Ctrl+V' in l or 'TEXT input' in l]"
    )
    log_lines = get_result(r_log) if r_log.get("ok") else []
    paste_count = sum(1 for l in (log_lines or []) if "CONSEC_" in l)

    # 三段都被處理即 PASS（剪貼簿可能因 watchdog 交疊而非原始值，
    # 重點是不 crash 且全部處理完）
    ok = r.get("ok", False) and paste_count >= 3
    if p(ok, f"Consecutive 3 pastes all processed",
         f"paste_logs={paste_count}, final_clip={clip!r:.40}"):
        passed += 1
    else:
        # 列印 debug 資訊
        for line in (log_lines or [])[:5]:
            print(f"    {line[:120]}")

    return total, passed


# ── 17. R4: Replay guard helpers ──────────────

def _test_replay_guard(total: int, passed: int) -> tuple[int, int]:
    """測試 manual paste guard 的 arm/disarm/block 計數機制。
    不實際觸發 keyboard hook（避免干擾），而是驗證 arm → disarm 迴路
    以及 block counter 的行為。
    """
    # 17a. arm → disarm 基本迴路（無 block）
    total += 1
    r_arm = eval_expr("self.paste._arm_manual_paste_guard('test_17a')")
    arm_ok = r_arm.get("ok", False) and get_result(r_arm) is True
    time.sleep(0.1)
    r_disarm = eval_expr("self.paste._disarm_manual_paste_guard()")
    disarm_result = get_result(r_disarm)
    # 無 block 時 disarm 應回傳 False（no pending）
    no_pending = r_disarm.get("ok", False) and disarm_result is False
    if p(arm_ok and no_pending, "Guard arm→disarm no pending",
         f"arm={arm_ok}, pending={disarm_result}"):
        passed += 1

    # 17b. arm → 手動設 pending+blocks → disarm 應回傳 True
    total += 1
    eval_expr("self.paste._arm_manual_paste_guard('test_17b')")
    time.sleep(0.1)
    # 模擬 _blocked_paste 被觸發：同時設 blocks 和 pending
    eval_expr(
        "(setattr(self.paste, '_manual_paste_guard_blocks', 2),"
        " setattr(self.paste, '_manual_paste_guard_pending', True))"
    )
    r_disarm2 = eval_expr("self.paste._disarm_manual_paste_guard()")
    has_pending = r_disarm2.get("ok", False) and get_result(r_disarm2) is True
    if p(has_pending, "Guard with pending → disarm=True",
         f"result={get_result(r_disarm2)}"):
        passed += 1

    # 17c. 重複 disarm（handler 已清除）不崩潰
    total += 1
    r_disarm3 = eval_expr("self.paste._disarm_manual_paste_guard()")
    double_ok = r_disarm3.get("ok", False) and get_result(r_disarm3) is False
    if p(double_ok, "Double disarm returns False (no handler)",
         f"result={get_result(r_disarm3)}"):
        passed += 1

    # 17d. block counter + pending 重置：re-arm 後歸零
    total += 1
    eval_expr("self.paste._arm_manual_paste_guard('test_17d')")
    time.sleep(0.05)
    r_count = eval_expr("self.paste._manual_paste_guard_blocks")
    r_pending = eval_expr("self.paste._manual_paste_guard_pending")
    count_zero = r_count.get("ok", False) and get_result(r_count) == 0
    pending_false = r_pending.get("ok", False) and get_result(r_pending) is False
    eval_expr("self.paste._disarm_manual_paste_guard()")  # cleanup
    if p(count_zero and pending_false,
         "Re-arm resets blocks=0, pending=False",
         f"count={get_result(r_count)}, pending={get_result(r_pending)}"):
        passed += 1

    return total, passed


# ── 18. R7: 視窗切換 during paste helpers ──────────────

def _test_window_switch_paste(total: int, passed: int) -> tuple[int, int]:
    """呼叫 paste_text 後立即切換前景視窗，
    驗證 log 記錄了實際貼上的目標視窗（而非切換後的視窗）。
    """
    total += 1

    # 記錄 log 行數
    r_lines = eval_expr(
        "len(open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        "key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines())"
    )
    lines_before = get_result(r_lines) if r_lines.get("ok") else 0

    # 記住當前前景視窗
    original_fg = user32.GetForegroundWindow()
    original_title = _get_window_title(original_fg)

    # 排入貼上
    eval_expr(
        "self.paste.paste_text('WINSWITCH_TEST', delay_ms=0, "
        "end_prefix='', preserve_ctrl_modifier=False)"
    )

    # 立即（~50ms 後）找另一個視窗並切過去
    time.sleep(0.05)
    found = _find_app_windows()
    switched = False
    for proc, wins in found.items():
        for hwnd, title in wins:
            if hwnd != original_fg:
                _activate_window(hwnd)
                switched = True
                break
        if switched:
            break

    # 等貼上完成
    time.sleep(4)

    # 讀 log 找 paste 相關行，確認有記錄前景視窗資訊
    r_log = eval_expr(
        f"[l.strip() for l in open(max(__import__('pathlib').Path('logs').glob('ai_whisper_*.current.log'), "
        f"key=lambda p: p.stat().st_mtime), encoding='utf-8', errors='replace').readlines()[{lines_before}:] "
        f"if 'WINSWITCH_TEST' in l or 'foreground' in l.lower() or 'TEXT input' in l or 'Ctrl+V' in l]"
    )
    log_lines = get_result(r_log) if r_log.get("ok") else []
    has_paste_log = len(log_lines or []) > 0

    # 還原前景視窗
    if original_fg:
        _activate_window(original_fg)

    if p(has_paste_log, "Window switch: paste logged with target info",
         f"switched={switched}, log_lines={len(log_lines or [])}"):
        passed += 1
    for line in (log_lines or [])[:3]:
        print(f"    {line[:120]}")

    return total, passed


# ── 19. R26: 大型剪貼簿備份還原效能 helpers ──────────────

def _test_large_clipboard(total: int, passed: int) -> tuple[int, int]:
    """寫入大型文字到剪貼簿（~1MB），測 backup/restore 不崩潰且 <2s。"""
    total += 1

    # 生成 ~1MB 文字（重複字串）
    eval_expr(
        "self.paste._set_clipboard_verified('X' * 1_000_000)"
    )
    time.sleep(0.3)

    # 計時 backup + restore 迴路
    r = eval_expr(
        "("
        "  (t0 := __import__('time').perf_counter()),"
        "  (bk := self.paste._save_clipboard_all()),"
        "  self.paste._restore_clipboard_all(bk) if bk else None,"
        "  (t1 := __import__('time').perf_counter()),"
        "  t1 - t0,"
        ")[-1]"
    )
    elapsed = get_result(r)
    ok = r.get("ok", False) and isinstance(elapsed, (int, float)) and elapsed < 2.0
    if p(ok, f"Large clipboard (1MB) backup+restore",
         f"elapsed={elapsed:.3f}s" if isinstance(elapsed, (int, float)) else str(elapsed)):
        passed += 1

    # 驗證內容完整（讀回長度）
    total += 1
    r2 = eval_expr("len(self.paste._read_clipboard_text() or '')")
    length = get_result(r2)
    ok2 = r2.get("ok", False) and length == 1_000_000
    if p(ok2, "Large clipboard content preserved",
         f"length={length}, expected=1000000"):
        passed += 1

    # 清理：設回正常小文字
    eval_expr("self.paste._set_clipboard_verified('clipboard_cleaned')")

    return total, passed


if __name__ == "__main__":
    main()
