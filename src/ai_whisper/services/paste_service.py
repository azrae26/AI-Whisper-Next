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
CLIPBOARD_GDI_FORMATS = {2, 3, 9, 14}
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
        self._init_clipboard_api()
        self._worker = threading.Thread(target=self._paste_worker, daemon=True, name="PasteWorker")
        self._worker.start()

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

    def _save_clipboard_all(self) -> list[tuple[int, bytes]] | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
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
            return items if items else None
        except Exception as e:
            _safe_print(f"[paster][{_now()}] ⚠️ 備份剪貼簿失敗: {e}")
            return None
        finally:
            user32.CloseClipboard()

    def _restore_clipboard_all(self, items: list[tuple[int, bytes]]) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return False
        try:
            user32.EmptyClipboard()
            for fmt, data in items:
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    continue
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    kernel32.GlobalFree(h)
                    continue
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(h)
                user32.SetClipboardData(fmt, h)
            return True
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
            return False
        try:
            user32.EmptyClipboard()
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                kernel32.GlobalFree(h)
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        finally:
            user32.CloseClipboard()

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
        old_clipboard = self._save_clipboard_all()
        cb_ok = self._set_clipboard_ctypes(final_text)
        if not cb_ok:
            _safe_print(f"[paster][{_now()}] ❌ [PASTE-FAIL] 剪貼簿寫入失敗，text={repr(final_text[:40])}")
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, buf, 128)
            win_title = buf.value
        except Exception:
            hwnd, win_title = 0, "(unknown)"
        _safe_print(f"[paster][{_now()}] ⌨️ Ctrl+V 送出，cb_ok={cb_ok}，視窗=\"{win_title}\"，hwnd={hwnd:#010x}，text={repr(final_text[:40])}")
        keyboard.send("ctrl+v")
        if t_received:
            _safe_print(f"[paster][{_now()}] ⏱️ 收到→貼上完成: {time.perf_counter() - t_received:.2f}s")
        time.sleep(0.40)
        if old_clipboard is not None:
            self._restore_clipboard_all(old_clipboard)
            _safe_print(f"[paster][{_now()}] 📋 剪貼簿已還原（{len(old_clipboard)} 種格式）")

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

