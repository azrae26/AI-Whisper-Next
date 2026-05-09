from __future__ import annotations

import ctypes
import threading
import time

import keyboard
from PySide6.QtCore import QObject, Signal

from ..logging_setup import now_str, safe_print

MODIFIERS = {
    "ctrl", "shift", "alt", "left ctrl", "right ctrl",
    "left shift", "right shift", "left alt", "right alt",
    "left windows", "right windows", "windows",
}
MOD_NORMALIZE = {
    "left ctrl": "ctrl", "right ctrl": "ctrl",
    "left shift": "shift", "right shift": "shift",
    "left alt": "alt", "right alt": "alt",
    "left windows": "windows", "right windows": "windows",
}
NAME_HOOK_KEYS = {"insert", "pause"}


def parse_hotkey(hk_str: str) -> tuple[list[str], str | None]:
    parts = [p.strip().lower() for p in hk_str.split("+") if p.strip()]
    mods = [p for p in parts if p in MODIFIERS]
    main_key = next((p for p in reversed(parts) if p not in MODIFIERS), None)
    return mods, main_key


class HotkeyService(QObject):
    toggle_requested = Signal(str)
    history_requested = Signal(int)
    capture_finished = Signal(str)
    capture_cancelled = Signal()

    HK_BASE_ID = 0xBFF0
    MOD_MAP = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004}

    def __init__(self):
        super().__init__()
        self._comma_hook_remove = None
        self._capturing = False
        self._capture_keys: set[str] = set()
        self._hk_thread = None
        self._hk_thread_id = 0

    def register(self, hotkey: str, hotkey_comma: str, history_hotkeys: list[str]) -> None:
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self._comma_hook_remove:
            try:
                self._comma_hook_remove()
            except Exception:
                pass
            self._comma_hook_remove = None

        try:
            hk_mods, hk_main = parse_hotkey(hotkey)
            hc_mods, hc_main = parse_hotkey(hotkey_comma)
            name_triggers: list[tuple[list[str], str, str]] = []

            if hk_main in NAME_HOOK_KEYS:
                name_triggers.append((hk_mods, hk_main, "。"))
            else:
                def _hk_fired(p="。"):
                    t = time.perf_counter()
                    safe_print(f"[main][{now_str()}] ⌨️ 熱鍵觸發，排入 after(0)")
                    self.toggle_requested.emit(p)
                    safe_print(f"[main][{now_str()}] ⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                keyboard.add_hotkey(hotkey, _hk_fired)

            if hc_main in NAME_HOOK_KEYS:
                name_triggers.append((hc_mods, hc_main, "，"))
            else:
                def _hk_comma_fired(p="，"):
                    t = time.perf_counter()
                    safe_print(f"[main][{now_str()}] ⌨️ 熱鍵觸發，排入 after(0)")
                    self.toggle_requested.emit(p)
                    safe_print(f"[main][{now_str()}] ⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                keyboard.add_hotkey(hotkey_comma, _hk_comma_fired)

            if name_triggers:
                def _on_name_hook(event, triggers=name_triggers):
                    if event.event_type != keyboard.KEY_DOWN:
                        return
                    name = event.name.lower() if event.name else ""
                    if not name:
                        return
                    for mods, expected_name, punct in triggers:
                        if name == expected_name and all(keyboard.is_pressed(m) for m in mods):
                            t = time.perf_counter()
                            safe_print(f"[main][{now_str()}] ⌨️ 熱鍵觸發（name hook），排入 after(0)")
                            self.toggle_requested.emit(punct)
                            safe_print(f"[main][{now_str()}] ⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                            break
                self._comma_hook_remove = keyboard.hook(_on_name_hook)

            safe_print(f"[main][{now_str()}] ✅ 快捷鍵 {hotkey}（句號）、{hotkey_comma}（逗號）已註冊")
        except Exception as e:
            safe_print(f"[main][{now_str()}] ❌ 快捷鍵註冊失敗: {e}")

        self.register_history_hotkeys(history_hotkeys)

    def start_capture(self) -> None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._capture_keys = set()
        self._capturing = True
        keyboard.hook(self._on_capture_event)

    def _on_capture_event(self, event) -> None:
        if not self._capturing:
            return
        name = event.name.lower() if event.name else ""
        if event.event_type == keyboard.KEY_DOWN:
            if name == "esc":
                self._capturing = False
                self.capture_cancelled.emit()
                return
            self._capture_keys.add(MOD_NORMALIZE.get(name, name))
        elif event.event_type == keyboard.KEY_UP:
            if self._capture_keys:
                keys = self._capture_keys.copy()
                self._capturing = False
                mod_order = ["ctrl", "shift", "alt", "windows"]
                mods = [k for k in mod_order if k in keys]
                others = sorted(k for k in keys if k not in mod_order)
                if others:
                    self.capture_finished.emit("+".join(mods + others))

    def finish_capture_cleanup(self) -> None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass

    @staticmethod
    def key_to_vk(name: str) -> int:
        k = name.lower().strip()
        if len(k) == 1 and k.isdigit():
            return ord(k)
        if len(k) == 1 and k.isalpha():
            return ord(k.upper())
        if k.startswith("f") and k[1:].isdigit():
            return 0x6F + int(k[1:])
        return {
            "space": 0x20,
            "enter": 0x0D,
            "tab": 0x09,
            "pause": 0x13,
            "escape": 0x1B,
            "backspace": 0x08,
            "delete": 0x2E,
        }.get(k, 0)

    @classmethod
    def parse_hotkey_win32(cls, hk_str: str) -> tuple[int, int]:
        parts = [p.strip().lower() for p in hk_str.split("+")]
        mods, vk = 0, 0
        for p in parts:
            if p in cls.MOD_MAP:
                mods |= cls.MOD_MAP[p]
            else:
                vk = cls.key_to_vk(p)
        return mods, vk

    def register_history_hotkeys(self, history_hotkeys: list[str]) -> None:
        old_tid = self._hk_thread_id
        old_thread = self._hk_thread
        if old_thread and old_thread.is_alive() and old_tid:
            ctypes.windll.user32.PostThreadMessageW(old_tid, 0x0012, 0, 0)
            old_thread.join(timeout=1.0)
        self._hk_thread_id = 0

        defaults = ["alt+shift+1", "alt+shift+2", "alt+shift+3", "alt+shift+4", "alt+shift+5"]
        parsed = []
        for i in range(5):
            hk = history_hotkeys[i] if i < len(history_hotkeys) else defaults[i]
            parsed.append(self.parse_hotkey_win32(hk) if hk else (0, 0))

        def _listener():
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.restype = ctypes.c_bool
            user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
            self._hk_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            ok_count = 0
            for i, (mods, vk) in enumerate(parsed):
                if not vk:
                    continue
                if user32.RegisterHotKey(None, self.HK_BASE_ID + i, mods | 0x4000, vk):
                    ok_count += 1
                else:
                    safe_print(f"[main][{now_str()}] ❌ Win32 記憶快捷鍵 {i + 1} 註冊失敗 (mods=0x{mods:X} vk=0x{vk:X})")
            safe_print(f"[main][{now_str()}] ✅ 記憶快捷鍵 {ok_count}/5 已註冊 (Win32)")
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    idx = msg.wParam - self.HK_BASE_ID
                    if 0 <= idx < 5:
                        self.history_requested.emit(int(idx))
            for i in range(5):
                user32.UnregisterHotKey(None, self.HK_BASE_ID + i)

        self._hk_thread = threading.Thread(target=_listener, daemon=True, name="HotkeyListener")
        self._hk_thread.start()

    def shutdown(self) -> None:
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self._comma_hook_remove:
            try:
                self._comma_hook_remove()
            except Exception:
                pass
        if self._hk_thread and self._hk_thread.is_alive() and self._hk_thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._hk_thread_id, 0x0012, 0, 0)

