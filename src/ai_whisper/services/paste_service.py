from __future__ import annotations

import ctypes
import datetime
import queue
import threading
import time

import keyboard
import uiautomation as auto

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYDOWN = 0
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
CTRL_VKS = (0x11, 0xA2, 0xA3)  # generic Ctrl, left Ctrl, right Ctrl
PASTE_MODIFIER_VKS = (
    0xA0, 0xA1,  # left Shift, right Shift
    0xA2, 0xA3,  # left Ctrl, right Ctrl
    0xA4, 0xA5,  # left Alt, right Alt
    0x5B, 0x5C,  # left Windows, right Windows
)
CLIPBOARD_GDI_FORMATS = {2, 3, 9, 14}
CLIPBOARD_SET_RETRIES = 4
CLIPBOARD_BACKUP_RETRIES = 8
CLIPBOARD_RETRY_DELAY_SEC = 0.06
CLIPBOARD_SETTLE_DELAY_SEC = 0.08
CLIPBOARD_RESTORE_DELAY_SEC = 0.30
CLIPBOARD_RESTORE_RETRIES = 4
CLIPBOARD_RESTORE_VERIFY_DELAY_SEC = 0.12
CLIPBOARD_WATCHDOG_DURATION_SEC = 2.20
CLIPBOARD_WATCHDOG_INTERVAL_SEC = 0.20
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


class PasteService:
    def __init__(self):
        self._paste_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._prefetch_lock = threading.Lock()
        self._prefetch_result: tuple | None = None
        self._manual_paste_guard_lock = threading.Lock()
        self._manual_paste_guard_handler = None
        self._manual_paste_guard_blocks = 0
        self._init_clipboard_api()
        self._worker = threading.Thread(target=self._paste_worker, daemon=True, name="PasteWorker")
        self._worker.start()

    def _arm_manual_paste_guard(self, pasted_text: str) -> bool:
        def _blocked_paste() -> None:
            with self._manual_paste_guard_lock:
                self._manual_paste_guard_blocks += 1
                blocks = self._manual_paste_guard_blocks
            _safe_print(
                f"[paster][{_now()}] 🚫 CLIP guard: blocked manual Ctrl+V "
                f"until restore completes (count={blocks}, temp={repr(pasted_text[:20])})"
            )

        try:
            with self._manual_paste_guard_lock:
                self._manual_paste_guard_blocks = 0
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

    def _disarm_manual_paste_guard(self) -> None:
        try:
            with self._manual_paste_guard_lock:
                handler = self._manual_paste_guard_handler
                blocks = self._manual_paste_guard_blocks
                self._manual_paste_guard_handler = None
                self._manual_paste_guard_blocks = 0
            if handler is not None:
                keyboard.remove_hotkey(handler)
                _safe_print(f"[paster][{_now()}] 🚫 CLIP guard disarmed: blocked={blocks}")
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ CLIP guard disarm failed: {e}")

    @staticmethod
    def _init_clipboard_api() -> None:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
        kernel32.GlobalSize.restype = ctypes.c_size_t
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
        user32.EnumClipboardFormats.restype = ctypes.c_uint

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
            time.sleep(CLIPBOARD_WATCHDOG_INTERVAL_SEC)
            checks += 1
            current = self._read_clipboard_text()
            if current == expected_text:
                continue
            if current == pasted_text:
                repairs += 1
                _safe_print(
                    f"[paster][{_now()}] ⚠️ CLIP watchdog re-restore: "
                    f"check={checks}, got_pasted={repr(current[:20])}"
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
                        f"attempt={attempt}, chars={len(current)}, text={repr(current[:20])}"
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
    def _release_paste_modifiers() -> list[int]:
        user32 = ctypes.windll.user32
        released: list[int] = []
        for vk in PASTE_MODIFIER_VKS:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                released.append(vk)
        return released

    @staticmethod
    def _restore_paste_modifiers(vks: list[int]) -> None:
        user32 = ctypes.windll.user32
        for vk in vks:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYDOWN, 0)

    @staticmethod
    def _force_release_ctrl_keys() -> None:
        user32 = ctypes.windll.user32
        for vk in CTRL_VKS:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    @classmethod
    def _send_ctrl_v(cls) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYDOWN, 0)
        try:
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYDOWN, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        finally:
            cls._force_release_ctrl_keys()

    @staticmethod
    def _is_cursor_at_end() -> tuple[bool, bool]:
        try:
            focused = auto.GetFocusedControl()
            if not focused:
                _safe_print(f"[paster][{_now()}] ⚠️ 無焦點控件")
                return (False, False)
            try:
                vp = focused.GetValuePattern()
                text = vp.Value or ""
            except Exception:
                text = ""
            if not text:
                _safe_print(f"[paster][{_now()}] 📏 [UIA] 文字為空 → 不加句號")
                return (False, False)
            try:
                tp = focused.GetTextPattern()
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

    def prefetch_cursor_position(self, wav_bytes_len: int = 0) -> None:
        audio_sec = max(0, (wav_bytes_len - 44)) / 32000 if wav_bytes_len > 44 else 0
        estimated_api = audio_sec * 0.10 + 0.25 if audio_sec <= 15 else audio_sec * 0.03 + 1.30
        prefetch_delay = max(0, estimated_api - 0.45)

        def _do_prefetch() -> None:
            if prefetch_delay > 0:
                time.sleep(prefetch_delay)
            import comtypes
            comtypes.CoInitialize()
            try:
                at_end, last_char_is_punctuation = self._is_cursor_at_end()
                with self._prefetch_lock:
                    self._prefetch_result = (time.perf_counter(), at_end, last_char_is_punctuation)
                _safe_print(f"[paster][{_now()}] 🔮 預取游標位置: at_end={at_end}, last_punct={last_char_is_punctuation} (delay={prefetch_delay:.2f}s, est_api={estimated_api:.2f}s)")
            finally:
                comtypes.CoUninitialize()

        threading.Thread(target=_do_prefetch, daemon=True, name="UIA-Prefetch").start()

    def _consume_prefetch(self, max_age: float = 10.0) -> tuple[bool, bool] | None:
        with self._prefetch_lock:
            if self._prefetch_result is None:
                return None
            ts, at_end, last_char_is_punctuation = self._prefetch_result
            self._prefetch_result = None
            if time.perf_counter() - ts > max_age:
                return None
            return (at_end, last_char_is_punctuation)

    def paste_text(self, text: str, delay_ms: int = 50, t_received: float = 0.0, end_prefix: str = "。") -> None:
        self._paste_queue.put((text, delay_ms, t_received, end_prefix))

    def _execute_paste(self, text: str, delay_ms: int, t_received: float, end_prefix: str) -> None:
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
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, buf, 128)
            win_title = buf.value
        except Exception:
            hwnd, win_title = 0, "(unknown)"
        _safe_print(f"[paster][{_now()}] ⌨️ Ctrl+V 送出，cb_ok={cb_ok}，視窗=\"{win_title}\"，hwnd={hwnd:#010x}，text={repr(final_text[:40])}")
        released_modifiers = self._release_paste_modifiers()
        modifiers_to_restore = [vk for vk in released_modifiers if vk not in CTRL_VKS]
        try:
            self._send_ctrl_v()
        finally:
            self._force_release_ctrl_keys()
            self._restore_paste_modifiers(modifiers_to_restore)
        guard_armed = self._arm_manual_paste_guard(final_text)
        if t_received:
            _safe_print(f"[paster][{_now()}] ⏱️ 收到→貼上完成: {time.perf_counter() - t_received:.2f}s")
        try:
            _safe_print(f"[paster][{_now()}] 📋 CLIP restore wait: {CLIPBOARD_RESTORE_DELAY_SEC:.2f}s")
            time.sleep(CLIPBOARD_RESTORE_DELAY_SEC)
            if old_clipboard is not None:
                restored = self._restore_clipboard_verified(old_clipboard)
                if restored and guard_armed:
                    self._disarm_manual_paste_guard()
                    guard_armed = False
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
