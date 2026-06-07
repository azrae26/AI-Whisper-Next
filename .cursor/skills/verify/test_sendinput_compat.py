r"""AI Whisper Next — SendInput 相容性掃描

自動切換畫面上所有可見視窗，逐一測試 SendInput UNICODE 是否能正常輸入。
透過 Debug Server 在 App 程序內執行 send_unicode_text，避免 UIPI 問題。

使用方式（從專案根目錄）：
  py -3.12 .agents/skills/verify/test_sendinput_compat.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "127.0.0.1"
PORT = 47643

# ── Debug Server 通訊 ──────────────────────────────────────

def query(method: str, params: dict | None = None, timeout: float = 5) -> dict:
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
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        return {"ok": False, "error": str(e)}


def eval_expr(expr: str, timeout: float = 5) -> dict:
    return query("eval", {"expr": expr}, timeout=timeout)


def get_result(r: dict):
    if "result" in r:
        return r["result"]
    if "value" in r:
        return r["value"]
    return r.get("error", "?")


# ── 視窗列舉 ───────────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_process_name(hwnd: int) -> str:
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


def enumerate_visible_windows() -> list[dict]:
    """列舉所有可見的頂層視窗。"""
    windows: list[dict] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_title(hwnd)
        if not title or title in ("Program Manager", "Settings"):
            return True
        # 跳過太小的視窗（工具列、通知等）
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 100 or h < 50:
            return True
        windows.append({
            "hwnd": hwnd,
            "title": title,
            "class": get_window_class(hwnd),
            "process": get_process_name(hwnd),
            "size": f"{w}x{h}",
        })
        return True

    user32.EnumWindows(callback, 0)
    return windows


def activate_window(hwnd: int) -> bool:
    """切換視窗到前景。"""
    # Alt up trick: 解除 SetForegroundWindow 限制
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    result = user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)  # 等視窗切到前景
    fg = user32.GetForegroundWindow()
    return fg == hwnd


def send_backspace_local(n: int = 1):
    """從本地發 Backspace 清理。"""
    for _ in range(n):
        user32.keybd_event(0x08, 0, 0, 0)
        user32.keybd_event(0x08, 0, 2, 0)
        time.sleep(0.02)


# ── 主測試流程 ──────────────────────────────────────────────

TEST_MARKER = "\u200b"  # zero-width space (不可見，不影響應用)
TEST_TEXT = "Tq"         # 簡短可見測試文字
CLEANUP_BS = len(TEST_TEXT)


def test_window(win: dict) -> dict:
    """對單一視窗測試 SendInput UNICODE。"""
    hwnd = win["hwnd"]
    result = {
        **win,
        "activated": False,
        "sendinput_ok": None,
        "note": "",
    }

    # 1. 切換到前景
    if not activate_window(hwnd):
        result["note"] = "activate failed"
        return result
    result["activated"] = True

    # 2. 透過 debug server 呼叫 send_unicode_text
    r = eval_expr(f"self.input.send_unicode_text({repr(TEST_TEXT)})")
    if not r.get("ok", False):
        result["note"] = f"eval error: {r.get('error', '?')[:50]}"
        return result

    ok = get_result(r)
    result["sendinput_ok"] = ok

    # 3. 清理：送 Backspace 刪掉測試文字
    time.sleep(0.15)
    # 用 debug server 送 backspace（在 app 程序內）
    eval_expr(
        f"[__import__('ctypes').windll.user32.keybd_event(0x08,0,0,0) "
        f"or __import__('ctypes').windll.user32.keybd_event(0x08,0,2,0) "
        f"or __import__('time').sleep(0.02) for _ in range({CLEANUP_BS})] and True"
    )

    if ok:
        result["note"] = "OK"
    else:
        result["note"] = "SendInput returned False (sent=0)"

    return result


def main():
    print("=" * 70)
    print("AI Whisper Next — SendInput UNICODE Compatibility Scan")
    print("=" * 70)

    # 檢查 App 存活
    r = query("ping")
    if not r.get("ok", False):
        print("App not running. Start AI Whisper first.")
        sys.exit(1)
    print(f"App alive (PID: {get_result(r).get('pid', '?')})\n")

    # 記住原始前景視窗
    original_fg = user32.GetForegroundWindow()

    # 只測試這些程式（白名單），沒開就不測
    TARGET_PROCESSES = {
        "chrome.exe",
        "line.exe",
        "cursor.exe",
        "antigravity.exe",
        "codex.exe",
    }

    # 列舉視窗，白名單過濾 + 同 process 去重
    all_windows = enumerate_visible_windows()
    seen_procs: set[str] = set()
    windows: list[dict] = []
    for w in all_windows:
        proc = w.get("process", "")
        if proc not in TARGET_PROCESSES:
            continue
        # LINE：只測對話視窗，跳過主視窗（標題為 "LINE"）
        if proc == "line.exe" and w.get("title", "").strip() == "LINE":
            continue
        if proc in seen_procs:
            continue
        seen_procs.add(proc)
        windows.append(w)

    matched = [p for p in TARGET_PROCESSES if p in seen_procs]
    missed = sorted(TARGET_PROCESSES - seen_procs)
    print(f"Target processes: {len(TARGET_PROCESSES)}, found: {len(matched)}")
    if missed:
        print(f"Not running (skipped): {', '.join(missed)}")
    print()

    # 逐一測試
    results: list[dict] = []
    for i, win in enumerate(windows):
        short_title = win["title"][:40]
        print(f"  [{i+1}/{len(windows)}] {win['process'] or '?':20s} | {short_title}...", end=" ", flush=True)
        try:
            res = test_window(win)
            results.append(res)
            icon = "[v]" if res["sendinput_ok"] else "[X]" if res["sendinput_ok"] is False else "[?]"
            print(f"{icon} {res['note']}")
        except Exception as e:
            print(f"[!] Error: {e}")
            results.append({**win, "activated": False, "sendinput_ok": None, "note": str(e)})
        time.sleep(0.3)

    # 切回原本視窗
    if original_fg:
        activate_window(original_fg)

    # ── 結果報表 ──
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Process':<25s} {'Class':<25s} {'SendInput':<10s} {'Title'}")
    print("-" * 70)

    ok_list = []
    fail_list = []
    skip_list = []

    for r in results:
        process = r.get("process", "?")[:24]
        cls = r.get("class", "?")[:24]
        title = r.get("title", "?")[:40]
        si = r.get("sendinput_ok")
        if si is True:
            status = "OK"
            ok_list.append(r)
        elif si is False:
            status = "FAIL"
            fail_list.append(r)
        else:
            status = "SKIP"
            skip_list.append(r)
        print(f"{process:<25s} {cls:<25s} {status:<10s} {title}")

    print("-" * 70)
    print(f"OK: {len(ok_list)}  |  FAIL: {len(fail_list)}  |  SKIP: {len(skip_list)}")

    if fail_list:
        print("\n[!] 以下程式 SendInput 失敗，建議加入黑名單:")
        processes = sorted(set(r.get("process", "") for r in fail_list))
        for p in processes:
            if p:
                print(f"    \"{p}\"")

    if not fail_list:
        print("\n[v] 所有視窗都支援 SendInput UNICODE，不需要黑名單！")

    print("=" * 70)


if __name__ == "__main__":
    main()
