from __future__ import annotations

import ctypes
import datetime
import os
import queue
import threading
import time
from typing import Any

import keyboard
import uiautomation as auto

from .input_service import InputService

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
CLIPBOARD_GDI_FORMATS = {2, 3, 9, 14}
CLIPBOARD_SET_RETRIES = 4
CLIPBOARD_BACKUP_RETRIES = 8

# M7: Win32 ctypes argtypes/restype — 只設一次（模組載入時）
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint),
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_user32.GetClipboardData.argtypes = [ctypes.c_uint]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
_user32.EnumClipboardFormats.restype = ctypes.c_uint
CLIPBOARD_RETRY_DELAY_SEC = 0.06
CLIPBOARD_SETTLE_DELAY_SEC = 0.08
CLIPBOARD_RESTORE_DELAY_SEC = 0.30
CLIPBOARD_RESTORE_RETRIES = 4
CLIPBOARD_RESTORE_VERIFY_DELAY_SEC = 0.12
CLIPBOARD_WATCHDOG_DURATION_SEC = 2.20
CLIPBOARD_WATCHDOG_INTERVAL_SEC = 0.20
UNICODE_INPUT_VERIFY_DELAY_SEC = 0.08
UNICODE_INPUT_VERIFY_BACKOFF_SEC = (0.06, 0.12, 0.24)  # H11: exponential backoff, max 3 attempts
UNICODE_INPUT_VERIFY_SUFFIX_CHARS = 2
DIRECT_TEXT_READABLE_MAX_CHARS = 40
UIA_TIMEOUT_SEC = 2.0
ENDING_PUNCTUATION = frozenset(
    "。，、；：？！. , ; : ? ! …"
    "．，；：？！"
    "—–-"
    "·'\"~"
)


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)
        except Exception:
            pass


def _uia_with_timeout(fn, default, timeout=UIA_TIMEOUT_SEC):
    """Execute a UIA call with timeout. Returns *default* if UIA hangs.

    ⚠️ 避免前景視窗掛起時 UIA COM 呼叫永久阻塞 PasteWorker。
    超時後被放棄的 daemon thread 不會阻止程式退出。
    """
    result_box = [default]
    def worker():
        import comtypes
        comtypes.CoInitialize()
        try:
            result_box[0] = fn()
        finally:
            comtypes.CoUninitialize()
    t = threading.Thread(target=worker, daemon=True, name="UIA-timeout")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        _safe_print(
            f"[paster][{_now()}] ⚠️ UIA 查詢超時 ({timeout}s)，使用備援值"
        )
        return default
    return result_box[0]


def _uia_read_focused_plain_text(control: object) -> tuple[bool, str]:
    """從聚焦控制項取純文字；以 getattr 呼叫 Pattern（類別 stub 常未宣告 Get*Pattern）。"""
    get_vp = getattr(control, "GetValuePattern", None)
    if callable(get_vp):
        try:
            vp = get_vp()
            return (True, (getattr(vp, "Value", None) or "") if vp is not None else "")
        except Exception:
            pass
    get_tp = getattr(control, "GetTextPattern", None)
    if callable(get_tp):
        try:
            tp: Any = get_tp()
            doc = tp.DocumentRange.GetText(-1)
            return (True, doc or "")
        except Exception:
            return (False, "")
    return (False, "")


class PasteService:
    def __init__(self, input_service: InputService):
        self.input = input_service
        self._paste_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._prefetch_lock = threading.Lock()
        self._prefetch_result: tuple | None = None
        self._prefetch_queue: queue.Queue = queue.Queue()
        self._manual_paste_guard_lock = threading.Lock()
        self._manual_paste_guard_handler = None
        self._manual_paste_guard_blocks = 0
        self._manual_paste_guard_pending = False
        self._worker = threading.Thread(target=self._paste_worker, daemon=True, name="PasteWorker")
        self._worker.start()
        self._prefetch_thread = threading.Thread(target=self._prefetch_worker, daemon=True, name="UIA-Prefetch")
        self._prefetch_thread.start()

    def _arm_manual_paste_guard(self, pasted_text: str) -> bool:
        def _blocked_paste() -> None:
            with self._manual_paste_guard_lock:
                self._manual_paste_guard_blocks += 1
                self._manual_paste_guard_pending = True
                blocks = self._manual_paste_guard_blocks
            _safe_print(
                f"[paster][{_now()}] 🚫 CLIP guard: blocked manual Ctrl+V "
                f"until restore completes (count={blocks}, replay=queued, temp={repr(pasted_text[:20])})"
            )

        try:
            with self._manual_paste_guard_lock:
                self._manual_paste_guard_blocks = 0
                self._manual_paste_guard_pending = False
                if self._manual_paste_guard_handler is not None:
                    keyboard.remove_hotkey(self._manual_paste_guard_handler)
                    self._manual_paste_guard_handler = None
                self._manual_paste_guard_handler = keyboard.add_hotkey(
                    "ctrl+v",
                    _blocked_paste,
                    suppress=True,
                    trigger_on_release=False,
                )
            _safe_print(f"[paster][{_now()}] 🚫 CLIP guard armed: manual Ctrl+V suppressed")
            return True
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ CLIP guard arm failed: {e}")
            return False

    def _disarm_manual_paste_guard(self) -> bool:
        try:
            with self._manual_paste_guard_lock:
                handler = self._manual_paste_guard_handler
                blocks = self._manual_paste_guard_blocks
                pending = self._manual_paste_guard_pending
                self._manual_paste_guard_handler = None
                self._manual_paste_guard_blocks = 0
                self._manual_paste_guard_pending = False
            if handler is not None:
                keyboard.remove_hotkey(handler)
                _safe_print(f"[paster][{_now()}] 🚫 CLIP guard disarmed: blocked={blocks}")
            return pending
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ CLIP guard disarm failed: {e}")
            return False

    def _replay_manual_paste_if_requested(self, pending: bool) -> None:
        if not pending:
            return
        _safe_print(f"[paster][{_now()}] 🚫 CLIP guard replay: manual Ctrl+V after restore")
        self.input.send_ctrl_v()
        time.sleep(0.08)

    @staticmethod
    def _init_clipboard_api() -> None:
        pass  # M7: moved to module-level declarations

    @staticmethod
    def _foreground_window() -> tuple[int, str, str, str]:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, buf, 128)
            cls_buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls_buf, 128)
            process_name = ""
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                process_name = PasteService._process_name_from_pid(pid.value)
            return hwnd, buf.value, process_name, cls_buf.value
        except Exception:
            return 0, "(unknown)", "", ""

    @staticmethod
    def _process_name_from_pid(pid: int) -> str:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        # M7: argtypes/restype 已在模組頂層設定
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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

    @staticmethod
    def _focused_control_signature() -> tuple[str, str, str, str]:
        # ⚠️ UIA timeout 防護：避免前景視窗掛起時永久阻塞 PasteWorker（H6）
        def _query():
            try:
                focused = auto.GetFocusedControl()
                if not focused:
                    return ("", "", "", "")
                return (
                    getattr(focused, "Name", "") or "",
                    getattr(focused, "ClassName", "") or "",
                    getattr(focused, "AutomationId", "") or "",
                    getattr(focused, "ControlTypeName", "") or "",
                )
            except Exception:
                return ("", "", "", "")
        return _uia_with_timeout(_query, ("", "", "", ""))

    @staticmethod
    def _is_chrome_omnibox(process_name: str, focus_sig: tuple[str, str, str, str]) -> bool:
        if process_name != "chrome.exe":
            return False
        needle = " ".join(focus_sig).lower()
        return any(
            token in needle
            for token in (
                "omnibox",
                "address",
                "search or type",
                "網址",
                "位址",
                "搜尋或輸入",
                "搜尋或輸入網址",
            )
        )

    @staticmethod
    def _should_use_direct_text_input(
        win_title: str,
        process_name: str,
        preserve_ctrl_modifier: bool,
        focus_sig: tuple[str, str, str, str],
    ) -> bool:
        if preserve_ctrl_modifier:
            return False
        if process_name in {"codex.exe", "cursor.exe", "antigravity.exe", "line.exe"}:
            return True
        title = win_title.lower()
        if win_title == "Codex" or win_title.startswith("Codex "):
            return True
        if " - cursor" in title or title.endswith(" cursor"):
            return True
        if "antigravity" in title:
            return True
        if PasteService._is_chrome_omnibox(process_name, focus_sig):
            return False
        return False

    @staticmethod
    def _focused_text_snapshot() -> tuple[bool, str]:
        # ⚠️ UIA timeout 防護（H6）
        def _query():
            try:
                focused = auto.GetFocusedControl()
                if not focused:
                    return (False, "")
                ok, txt = _uia_read_focused_plain_text(focused)
                return (True, txt) if ok else (False, "")
            except Exception:
                return (False, "")
        return _uia_with_timeout(_query, (False, ""))

    @staticmethod
    def _verify_direct_text_input(
        before_readable: bool,
        before_text: str,
        final_text: str,
        at_end: bool,
    ) -> tuple[bool, str]:
        # H11: exponential backoff (60→120→240ms), max 3 attempts
        suffix_len = min(UNICODE_INPUT_VERIFY_SUFFIX_CHARS, len(final_text))
        suffix = final_text[-suffix_len:] if suffix_len > 0 else ""
        for attempt, delay in enumerate(UNICODE_INPUT_VERIFY_BACKOFF_SEC):
            after_readable, after_text = PasteService._focused_text_snapshot()
            if not before_readable or not after_readable:
                return (True, "unreadable")
            if after_text != before_text:
                if not at_end or not suffix or after_text.endswith(suffix):
                    return (True, "changed")
                if final_text in after_text:
                    return (True, "changed_contains")
                # Text changed but suffix doesn't match — last attempt gives up
                if attempt == len(UNICODE_INPUT_VERIFY_BACKOFF_SEC) - 1:
                    return (
                        True,
                        f"changed_suffix_unverified:{repr(after_text[-suffix_len:])}!={repr(suffix)}",
                    )
            else:
                # Text unchanged — if this is already the 2nd+ attempt, give up early
                if attempt >= 1:
                    return (False, "unchanged")
            time.sleep(delay)
        # Exhausted all attempts with no change detected
        return (False, "unchanged")

    @staticmethod
    def _is_hglobal_format(fmt: int) -> bool:
        if fmt in CLIPBOARD_GDI_FORMATS:
            return False
        if 0x0300 <= fmt <= 0x03FF:
            return False
        return True

    @staticmethod
    def _clipboard_items_summary(items: list[tuple[int, bytes]] | None, limit: int = 8) -> str:
        if not items:
            return "none"
        parts = [f"{fmt}:{len(data)}B" for fmt, data in items[:limit]]
        if len(items) > limit:
            parts.append(f"+{len(items) - limit} more")
        return ", ".join(parts)

    @staticmethod
    def _clipboard_text_from_items(items: list[tuple[int, bytes]] | None) -> str | None:
        if not items:
            return None
        for fmt, data in items:
            if fmt != CF_UNICODETEXT:
                continue
            try:
                return data.decode("utf-16-le", errors="replace").rstrip("\0")
            except Exception:
                return None
        return None

    @classmethod
    def _clipboard_text_preview_from_items(cls, items: list[tuple[int, bytes]] | None, limit: int = 20) -> str:
        text = cls._clipboard_text_from_items(items)
        if text is None:
            return "none" if not items else "missing"
        return repr(text[:limit]) if text else "empty"

    def _save_clipboard_all(self) -> list[tuple[int, bytes]] | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        opened = False
        for attempt in range(1, CLIPBOARD_BACKUP_RETRIES + 1):
            if user32.OpenClipboard(0):
                opened = True
                if attempt > 1:
                    _safe_print(f"[paster][{_now()}] 📋 CLIP backup: OpenClipboard ok attempt={attempt}")
                break
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP backup: OpenClipboard failed "
                f"attempt={attempt}/{CLIPBOARD_BACKUP_RETRIES}"
            )
            time.sleep(CLIPBOARD_RETRY_DELAY_SEC)
        if not opened:
            return None
        try:
            items: list[tuple[int, bytes]] = []
            fmt = user32.EnumClipboardFormats(0)
            while fmt:
                if self._is_hglobal_format(fmt):
                    h = user32.GetClipboardData(fmt)
                    if h:
                        ptr = kernel32.GlobalLock(h)
                        if ptr:
                            try:
                                size = kernel32.GlobalSize(h)
                                if size > 0:
                                    items.append((fmt, ctypes.string_at(ptr, size)))
                            finally:
                                kernel32.GlobalUnlock(h)
                fmt = user32.EnumClipboardFormats(fmt)
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP backup: "
                f"count={len(items)}, formats={self._clipboard_items_summary(items)}, "
                f"text={self._clipboard_text_preview_from_items(items)}"
            )
            return items
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ 備份剪貼簿失敗: {e}")
            return None
        finally:
            user32.CloseClipboard()

    def _restore_clipboard_verified(self, items: list[tuple[int, bytes]]) -> bool:
        expected_text = self._clipboard_text_from_items(items)
        for attempt in range(1, CLIPBOARD_RESTORE_RETRIES + 1):
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP restore attempt "
                f"{attempt}/{CLIPBOARD_RESTORE_RETRIES}: text={self._clipboard_text_preview_from_items(items)}"
            )
            api_ok = self._restore_clipboard_all(items)
            time.sleep(CLIPBOARD_RESTORE_VERIFY_DELAY_SEC)
            current = self._read_clipboard_text()
            text_ok = current == expected_text
            if api_ok and text_ok:
                _safe_print(
                    f"[paster][{_now()}] 📋 CLIP restore verify ok: "
                    f"attempt={attempt}, text={repr((current or '')[:20])}"
                )
                return True
            _safe_print(
                f"[paster][{_now()}] ⚠️ CLIP restore verify failed: "
                f"attempt={attempt}, api_ok={api_ok}, got={repr((current or '')[:20])}, "
                f"want={repr((expected_text or '')[:20])}"
            )
            time.sleep(CLIPBOARD_RESTORE_VERIFY_DELAY_SEC)
        return False

    def _watch_clipboard_restore(self, items: list[tuple[int, bytes]], pasted_text: str) -> bool:
        expected_text = self._clipboard_text_from_items(items)
        deadline = time.perf_counter() + CLIPBOARD_WATCHDOG_DURATION_SEC
        checks = 0
        repairs = 0
        while time.perf_counter() < deadline:
            if not self._paste_queue.empty():
                _safe_print(
                    f"[paster][{_now()}] 📋 CLIP watchdog skip: "
                    f"pending paste queued, checks={checks}, repairs={repairs}"
                )
                return True
            time.sleep(CLIPBOARD_WATCHDOG_INTERVAL_SEC)
            checks += 1
            current = self._read_clipboard_text()
            if current == expected_text:
                continue
            if current == pasted_text:
                repairs += 1
                _safe_print(
                    f"[paster][{_now()}] ⚠️ CLIP watchdog re-restore: "
                    f"check={checks}, got_pasted={repr((current or '')[:20])}"
                )
                self._restore_clipboard_verified(items)
                continue
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP watchdog stop: external clipboard change, "
                f"check={checks}, got={repr((current or '')[:20])}, "
                f"expected={repr((expected_text or '')[:20])}"
            )
            return True
        final = self._read_clipboard_text()
        ok = final == expected_text
        _safe_print(
            f"[paster][{_now()}] 📋 CLIP watchdog done: "
            f"checks={checks}, repairs={repairs}, ok={ok}, final={repr((final or '')[:20])}"
        )
        return ok

    def _restore_clipboard_all(self, items: list[tuple[int, bytes]]) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            _safe_print(f"[paster][{_now()}] 📋 CLIP restore: OpenClipboard failed")
            return False
        try:
            user32.EmptyClipboard()
            restored = 0
            for fmt, data in items:
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    _safe_print(f"[paster][{_now()}] 📋 CLIP restore: GlobalAlloc failed fmt={fmt}")
                    continue
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    kernel32.GlobalFree(h)
                    _safe_print(f"[paster][{_now()}] 📋 CLIP restore: GlobalLock failed fmt={fmt}")
                    continue
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h)
                if user32.SetClipboardData(fmt, h):
                    restored += 1
                else:
                    kernel32.GlobalFree(h)
                    _safe_print(f"[paster][{_now()}] 📋 CLIP restore: SetClipboardData failed fmt={fmt}")
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP restore: "
                f"restored={restored}/{len(items)}, formats={self._clipboard_items_summary(items)}, "
                f"text={self._clipboard_text_preview_from_items(items)}"
            )
            return restored == len(items)
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ 還原剪貼簿失敗: {e}")
            return False
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _set_clipboard_ctypes(text: str) -> bool:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        data = (text + "\0").encode("utf-16-le")
        if not user32.OpenClipboard(0):
            _safe_print(f"[paster][{_now()}] 📋 CLIP set: OpenClipboard failed")
            return False
        try:
            user32.EmptyClipboard()
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                _safe_print(f"[paster][{_now()}] 📋 CLIP set: GlobalAlloc failed bytes={len(data)}")
                return False
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                kernel32.GlobalFree(h)
                _safe_print(f"[paster][{_now()}] 📋 CLIP set: GlobalLock failed bytes={len(data)}")
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                kernel32.GlobalFree(h)
                _safe_print(f"[paster][{_now()}] 📋 CLIP set: SetClipboardData failed bytes={len(data)}")
                return False
            return True
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _read_clipboard_text() -> str | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            _safe_print(f"[paster][{_now()}] 📋 CLIP read: OpenClipboard failed")
            return None
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                _safe_print(f"[paster][{_now()}] 📋 CLIP read: CF_UNICODETEXT missing")
                return None
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                _safe_print(f"[paster][{_now()}] 📋 CLIP read: GlobalLock failed")
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(h)
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ 讀取剪貼簿驗證失敗: {e}")
            return None
        finally:
            user32.CloseClipboard()

    def _set_clipboard_verified(self, text: str) -> bool:
        for attempt in range(1, CLIPBOARD_SET_RETRIES + 1):
            _safe_print(
                f"[paster][{_now()}] 📋 CLIP set attempt {attempt}/{CLIPBOARD_SET_RETRIES}: "
                f"chars={len(text)}, bytes={(len(text) + 1) * 2}, preview={repr(text[:40])}"
            )
            if self._set_clipboard_ctypes(text):
                time.sleep(CLIPBOARD_RETRY_DELAY_SEC)
                current = self._read_clipboard_text()
                if current == text:
                    _safe_print(
                        f"[paster][{_now()}] 📋 CLIP verify ok: "
                        f"attempt={attempt}, chars={len(text)}, text={repr(text[:20])}"
                    )
                    return True
                _safe_print(
                    f"[paster][{_now()}] ⚠️ 剪貼簿驗證不符 "
                    f"(attempt={attempt}, got={repr((current or '')[:40])}, want={repr(text[:40])})"
                )
            else:
                _safe_print(f"[paster][{_now()}] ⚠️ 剪貼簿寫入失敗 (attempt={attempt})")
            time.sleep(CLIPBOARD_RETRY_DELAY_SEC)
        return False

    @staticmethod
    def _is_cursor_at_end() -> tuple[bool, bool]:
        # ⚠️ UIA timeout 防護（H6）
        def _query():
            try:
                focused = auto.GetFocusedControl()
                if not focused:
                    _safe_print(f"[paster][{_now()}] ⚠️ 無焦點控件")
                    return (False, False)
                text = ""
                get_vp = getattr(focused, "GetValuePattern", None)
                if callable(get_vp):
                    try:
                        vp = get_vp()
                        text = getattr(vp, "Value", None) or "" if vp is not None else ""
                    except Exception:
                        text = ""
                if not text:
                    _safe_print(f"[paster][{_now()}] 📏 [UIA] 文字為空 → 不加句號")
                    return (False, False)
                get_tp = getattr(focused, "GetTextPattern", None)
                if not callable(get_tp):
                    _safe_print(f"[paster][{_now()}] ⚠️ [UIA] 無 TextPattern")
                    return (False, False)
                try:
                    tp: Any = get_tp()
                    doc_range = tp.DocumentRange
                    sel = tp.GetSelection()
                    if not sel:
                        _safe_print(f"[paster][{_now()}] ⚠️ [UIA] GetSelection 為空")
                        return (False, False)
                    caret = sel[0]
                    after_range = doc_range.Clone()
                    after_range.MoveEndpointByRange(0, caret, 1)
                    text_after = after_range.GetText(-1)
                    at_end = len(text_after) == 0
                    stripped = text.rstrip()
                    last_char_is_punctuation = bool(stripped and stripped[-1] in ENDING_PUNCTUATION)
                    _safe_print(f"[paster][{_now()}] 📏 [UIA] text={repr(text[:20])}, text_after={repr(text_after[:20])}, at_end={at_end}, last_punct={last_char_is_punctuation}")
                    return (at_end, last_char_is_punctuation)
                except Exception as e:
                    _safe_print(f"[paster][{_now()}] ⚠️ [UIA] TextPattern 不支援: {e}")
                    return (False, False)
            except Exception as e:
                _safe_print(f"[paster][{_now()}] ⚠️ [UIA] 錯誤: {e}")
                return (False, False)
        return _uia_with_timeout(_query, (False, False))

    def _prefetch_worker(self) -> None:
        """Persistent worker thread for UIA prefetch; reuses COM init."""
        import comtypes
        comtypes.CoInitialize()
        try:
            while True:
                task = self._prefetch_queue.get()
                if task is None:
                    break
                prefetch_delay, estimated_api = task
                try:
                    if prefetch_delay > 0:
                        time.sleep(prefetch_delay)
                    at_end, last_char_is_punctuation = self._is_cursor_at_end()
                    with self._prefetch_lock:
                        self._prefetch_result = (time.perf_counter(), at_end, last_char_is_punctuation)
                    _safe_print(f"[paster][{_now()}] 🔮 預取游標位置: at_end={at_end}, last_punct={last_char_is_punctuation} (delay={prefetch_delay:.2f}s, est_api={estimated_api:.2f}s)")
                except Exception as e:
                    _safe_print(f"[paster][{_now()}] ⚠️ 預取游標位置失敗: {e}")
                finally:
                    self._prefetch_queue.task_done()
        finally:
            comtypes.CoUninitialize()

    def prefetch_cursor_position(self, wav_bytes_len: int = 0) -> None:
        audio_sec = max(0, (wav_bytes_len - 44)) / 32000 if wav_bytes_len > 44 else 0
        estimated_api = audio_sec * 0.10 + 0.25 if audio_sec <= 15 else audio_sec * 0.03 + 1.30
        prefetch_delay = max(0, estimated_api - 0.45)
        self._prefetch_queue.put((prefetch_delay, estimated_api))

    def _consume_prefetch(self, max_age: float = 10.0) -> tuple[bool, bool] | None:
        with self._prefetch_lock:
            if self._prefetch_result is None:
                return None
            ts, at_end, last_char_is_punctuation = self._prefetch_result
            self._prefetch_result = None
            if time.perf_counter() - ts > max_age:
                return None
            return (at_end, last_char_is_punctuation)

    def paste_text(
        self,
        text: str,
        delay_ms: int = 50,
        t_received: float = 0.0,
        end_prefix: str = "。",
        preserve_ctrl_modifier: bool = False,
    ) -> None:
        self._paste_queue.put((text, delay_ms, t_received, end_prefix, preserve_ctrl_modifier))

    def _execute_paste(
        self,
        text: str,
        delay_ms: int,
        t_received: float,
        end_prefix: str,
        preserve_ctrl_modifier: bool,
    ) -> None:
        prefetched = self._consume_prefetch()
        if prefetched is not None:
            at_end, last_char_is_punctuation = prefetched
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)
            add_prefix = at_end and not last_char_is_punctuation
            _safe_print(f"[paster][{_now()}] 🎯 PASTE: at_end={at_end}, last_punct={last_char_is_punctuation}, add_prefix={add_prefix} (prefetched), prefix={repr(end_prefix)}, final={repr(text[:40])}")
        else:
            t0 = time.perf_counter()
            at_end, last_char_is_punctuation = self._is_cursor_at_end()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            remaining = delay_ms - elapsed_ms
            if remaining > 0:
                time.sleep(remaining / 1000)
            add_prefix = at_end and not last_char_is_punctuation
            _safe_print(f"[paster][{_now()}] 🎯 PASTE: at_end={at_end}, last_punct={last_char_is_punctuation}, add_prefix={add_prefix}, prefix={repr(end_prefix)}, uia={elapsed_ms:.0f}ms, final={repr(text[:40])}")

        final_text = (end_prefix + text) if add_prefix else text
        hwnd, win_title, process_name, class_name = self._foreground_window()
        focus_sig = self._focused_control_signature()
        use_direct_text = self._should_use_direct_text_input(
            win_title,
            process_name,
            preserve_ctrl_modifier,
            focus_sig,
        )
        before_readable: bool = False
        before_text: str = ""
        if use_direct_text:
            before_readable, before_text = self._focused_text_snapshot()
            if before_readable and len(final_text) > DIRECT_TEXT_READABLE_MAX_CHARS:
                _safe_print(
                    f"[paster][{_now()}] ⌨️ TEXT input skipped: "
                    f"readable target long text ({len(final_text)}>{DIRECT_TEXT_READABLE_MAX_CHARS})，"
                    f"process={process_name or '?'}，class={class_name or '?'}，focus={focus_sig}"
                )
                use_direct_text = False
        if use_direct_text:
            _safe_print(
                f"[paster][{_now()}] ⌨️ TEXT input flow start: "
                f"target_chars={len(final_text)}，視窗=\"{win_title}\"，hwnd={hwnd:#010x}，"
                f"process={process_name or '?'}，class={class_name or '?'}，"
                f"focus={focus_sig}，"
                f"verify_readable={before_readable}，keys_before={self.input.modifier_state_summary()}，"
                f"text={repr(final_text[:40])}"
            )
        else:
            _safe_print(
                f"[paster][{_now()}] ⌨️ TEXT input skipped: "
                f"process={process_name or '?'}，class={class_name or '?'}，focus={focus_sig}，"
                f"preserve_ctrl={preserve_ctrl_modifier}"
            )
        if use_direct_text:
            released_modifiers = self.input.release_modifiers_for_paste(preserve_ctrl=False)
            _safe_print(
                f"[paster][{_now()}] ⌨️ 直接輸入前釋放修飾鍵: "
                f"released={self.input.vk_list(released_modifiers)}，"
                f"keys_after_release={self.input.modifier_state_summary()}"
            )
            try:
                ok = self.input.send_unicode_text(final_text)
            finally:
                self.input.force_release_ctrl()
                self.input.restore_modifiers([vk for vk in released_modifiers if not self.input.is_ctrl_vk(vk)])
            _safe_print(
                f"[paster][{_now()}] ⌨️ TEXT input done: "
                f"ok={ok}，keys_after_send={self.input.modifier_state_summary()}"
            )
            if t_received:
                _safe_print(f"[paster][{_now()}] ⏱️ 收到→直接輸入完成: {time.perf_counter() - t_received:.2f}s")
            verified = False
            verify_reason = "send_failed"
            if ok:
                time.sleep(UNICODE_INPUT_VERIFY_DELAY_SEC)
                verified, verify_reason = self._verify_direct_text_input(
                    before_readable,
                    before_text,
                    final_text,
                    at_end,
                )
            _safe_print(
                f"[paster][{_now()}] ⌨️ TEXT input verify: "
                f"ok={verified}，reason={verify_reason}"
            )
            if ok and verified:
                return
            _safe_print(f"[paster][{_now()}] ⚠️ TEXT input failed/unaccepted，fallback to clipboard Ctrl+V")

        _safe_print(
            f"[paster][{_now()}] 📋 CLIP flow start: "
            f"target_chars={len(final_text)}, restore_delay={CLIPBOARD_RESTORE_DELAY_SEC:.2f}s"
        )
        old_clipboard = self._save_clipboard_all()
        if old_clipboard is None:
            _safe_print(
                f"[paster][{_now()}] ❌ [PASTE-FAIL] 無法備份剪貼簿，取消 Ctrl+V，"
                f"text={repr(final_text[:40])}"
            )
            return
        cb_ok = self._set_clipboard_verified(final_text)
        if not cb_ok:
            _safe_print(f"[paster][{_now()}] ❌ [PASTE-FAIL] 剪貼簿未成功切換，取消 Ctrl+V，text={repr(final_text[:40])}")
            if old_clipboard is not None:
                restored = self._restore_clipboard_verified(old_clipboard)
                _safe_print(f"[paster][{_now()}] 📋 CLIP restore after failed set: ok={restored}")
            return
        time.sleep(CLIPBOARD_SETTLE_DELAY_SEC)
        hwnd, win_title, process_name, class_name = self._foreground_window()
        _safe_print(
            f"[paster][{_now()}] ⌨️ Ctrl+V 準備送出，cb_ok={cb_ok}，"
            f"視窗=\"{win_title}\"，hwnd={hwnd:#010x}，"
            f"process={process_name or '?'}，class={class_name or '?'}，"
            f"keys_before={self.input.modifier_state_summary()}，text={repr(final_text[:40])}"
        )
        released_modifiers = self.input.release_modifiers_for_paste(preserve_ctrl=preserve_ctrl_modifier)
        ctrl_preserved = preserve_ctrl_modifier and self.input.ctrl_state_down()
        modifiers_to_restore = [vk for vk in released_modifiers if not self.input.is_ctrl_vk(vk)]
        _safe_print(
            f"[paster][{_now()}] ⌨️ 貼上前釋放修飾鍵: "
            f"released={self.input.vk_list(released_modifiers)}，"
            f"restore_later={self.input.vk_list(modifiers_to_restore)}，"
            f"preserve_ctrl={ctrl_preserved}，keys_after_release={self.input.modifier_state_summary()}"
        )
        if ctrl_preserved:
            try:
                self.input.send_v()
            finally:
                self.input.restore_modifiers(modifiers_to_restore)
        else:
            try:
                self.input.send_ctrl_v()
            finally:
                self.input.force_release_ctrl()
                self.input.restore_modifiers(modifiers_to_restore)
        _safe_print(
            f"[paster][{_now()}] ⌨️ Ctrl+V 已送出，"
            f"keys_after_send={self.input.modifier_state_summary()}"
        )
        if not ctrl_preserved and self.input.ctrl_state_down():
            self.input.cleanup_ctrl_now("post-paste-immediate")
        guard_armed = self._arm_manual_paste_guard(final_text)
        if t_received:
            _safe_print(f"[paster][{_now()}] ⏱️ 收到→貼上完成: {time.perf_counter() - t_received:.2f}s")
        try:
            _safe_print(f"[paster][{_now()}] 📋 CLIP restore wait: {CLIPBOARD_RESTORE_DELAY_SEC:.2f}s")
            time.sleep(CLIPBOARD_RESTORE_DELAY_SEC)
            if old_clipboard is not None:
                restored = self._restore_clipboard_verified(old_clipboard)
                if restored and guard_armed:
                    pending_manual_paste = self._disarm_manual_paste_guard()
                    guard_armed = False
                    self._replay_manual_paste_if_requested(pending_manual_paste)
                watched = self._watch_clipboard_restore(old_clipboard, final_text) if restored else False
                _safe_print(
                    f"[paster][{_now()}] 📋 剪貼簿已還原驗證"
                    f"（{len(old_clipboard)} 種格式，restore_ok={restored}, watch_ok={watched}）"
                )
            else:
                _safe_print(f"[paster][{_now()}] 📋 CLIP restore skipped: no backup")
        finally:
            if guard_armed:
                self._disarm_manual_paste_guard()

    def _paste_worker(self) -> None:
        import comtypes
        comtypes.CoInitialize()
        try:
            while True:
                job = self._paste_queue.get()
                if job is None:
                    break
                self._execute_paste(*job)
        finally:
            comtypes.CoUninitialize()

    def shutdown(self) -> None:
        """Send sentinel values to worker threads so they exit gracefully."""
        self._paste_queue.put(None)
        self._prefetch_queue.put(None)
        self._worker.join(timeout=5)
        self._prefetch_thread.join(timeout=5)
