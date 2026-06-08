from __future__ import annotations

import ctypes
import threading
import time

import keyboard
from PySide6.QtCore import QObject, Signal

from ..logging_setup import log_prefix, now_str, safe_print
from .input_service import InputService

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

# Physical key names by virtual key code (unaffected by Shift)
VK_TO_NAME: dict[int, str] = {
    0x08: 'backspace', 0x09: 'tab', 0x0D: 'enter', 0x13: 'pause',
    0x1B: 'escape', 0x20: 'space',
    0x21: 'page up', 0x22: 'page down', 0x23: 'end', 0x24: 'home',
    0x25: 'left', 0x26: 'up', 0x27: 'right', 0x28: 'down',
    0x2D: 'insert', 0x2E: 'delete',
    0xBA: ';', 0xBB: '=', 0xBC: ',', 0xBD: '-', 0xBE: '.',
    0xBF: '/', 0xC0: '`', 0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'",
}
for _i in range(10):
    VK_TO_NAME[0x30 + _i] = str(_i)
for _i in range(26):
    VK_TO_NAME[0x41 + _i] = chr(0x61 + _i)
for _i in range(1, 25):
    VK_TO_NAME[0x6F + _i] = f'f{_i}'


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
    MOD_MAP = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "windows": 0x0008, "win": 0x0008}

    def __init__(self, input_service: InputService):
        super().__init__()
        self.input = input_service
        self._capturing = False
        self._capture_keys: set[str] = set()
        self._hk_thread = None
        self._hk_thread_id = 0

    def register(self, hotkey: str, hotkey_comma: str, history_hotkeys: list[str]) -> None:
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self._stop_win32_hotkeys()

        try:
            hk_mods, _hk_main = parse_hotkey(hotkey)
            hc_mods, _hc_main = parse_hotkey(hotkey_comma)
            win32_main_hotkeys: list[tuple[str, str, str, int, int, str]] = []

            hk_win32_mods, hk_win32_vk = self.parse_hotkey_win32(hotkey)
            if hk_win32_vk:
                win32_main_hotkeys.append(("main", "。", "句號", hk_win32_mods, hk_win32_vk, hotkey))
            else:
                def _hk_fired(p="。"):
                    t = time.perf_counter()
                    safe_print(
                        f"{log_prefix('[main]', now_str())}⌨️ 熱鍵觸發，排入 after(0)，"
                        f"keys={self.input.modifier_state_summary()}"
                    )
                    self.toggle_requested.emit(p)
                    self.input.schedule_hotkey_modifier_cleanup(hk_mods, "main")
                    safe_print(f"{log_prefix('[main]', now_str())}⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                keyboard.add_hotkey(hotkey, _hk_fired)

            hc_win32_mods, hc_win32_vk = self.parse_hotkey_win32(hotkey_comma)
            if hc_win32_vk:
                win32_main_hotkeys.append(("comma", "，", "逗號", hc_win32_mods, hc_win32_vk, hotkey_comma))
            else:
                def _hk_comma_fired(p="，"):
                    t = time.perf_counter()
                    safe_print(
                        f"{log_prefix('[main]', now_str())}⌨️ 熱鍵觸發，排入 after(0)，"
                        f"keys={self.input.modifier_state_summary()}"
                    )
                    self.toggle_requested.emit(p)
                    self.input.schedule_hotkey_modifier_cleanup(hc_mods, "comma")
                    safe_print(f"{log_prefix('[main]', now_str())}⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                keyboard.add_hotkey(hotkey_comma, _hk_comma_fired)

            safe_print(f"{log_prefix('[main]', now_str())}✅ 快捷鍵 {hotkey}（句號）、{hotkey_comma}（逗號）已註冊")
            self.register_win32_hotkeys(win32_main_hotkeys, history_hotkeys)
        except Exception as e:
            safe_print(f"{log_prefix('[main]', now_str())}❌ 快捷鍵註冊失敗: {e}")

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
            if name in MODIFIERS:
                self._capture_keys.add(MOD_NORMALIZE.get(name, name))
            else:
                self._capture_keys.add(self._resolve_physical_key(event.scan_code, name))
        elif event.event_type == keyboard.KEY_UP:
            if self._capture_keys:
                keys = self._capture_keys.copy()
                self._capturing = False
                mod_order = ["ctrl", "shift", "alt", "windows"]
                mods = [k for k in mod_order if k in keys]
                others = sorted(k for k in keys if k not in mod_order)
                if others:
                    self.capture_finished.emit("+".join(mods + others))

    @staticmethod
    def _resolve_physical_key(scan_code: int, fallback: str) -> str:
        """Map scan_code → VK → key name, bypassing Shift-modified characters."""
        if scan_code:
            vk = ctypes.windll.user32.MapVirtualKeyW(scan_code, 1)  # MAPVK_VSC_TO_VK
            if vk and vk in VK_TO_NAME:
                return VK_TO_NAME[vk]
        return fallback

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
            "page up": 0x21,
            "page down": 0x22,
            "end": 0x23,
            "home": 0x24,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
            "insert": 0x2D,
            "escape": 0x1B,
            "backspace": 0x08,
            "delete": 0x2E,
        }.get(k, 0)

    @classmethod
    def parse_hotkey_win32(cls, hk_str: str) -> tuple[int, int]:
        parts = [p.strip().lower() for p in hk_str.split("+")]
        mods, vk = 0, 0
        for p in parts:
            normalized = MOD_NORMALIZE.get(p, p)
            if normalized in cls.MOD_MAP:
                mods |= cls.MOD_MAP[normalized]
            else:
                vk = cls.key_to_vk(normalized)
        return mods, vk

    def _stop_win32_hotkeys(self) -> None:
        old_tid = self._hk_thread_id
        old_thread = self._hk_thread
        if old_thread and old_thread.is_alive() and old_tid:
            ctypes.windll.user32.PostThreadMessageW(old_tid, 0x0012, 0, 0)
            old_thread.join(timeout=1.0)
        self._hk_thread_id = 0
        self._hk_thread = None

    def register_win32_hotkeys(
        self,
        main_hotkeys: list[tuple[str, str, str, int, int, str]],
        history_hotkeys: list[str],
    ) -> None:
        self._stop_win32_hotkeys()

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
            registered_ids: list[int] = []
            main_id_to_hotkey: dict[int, tuple[str, str, str]] = {}
            history_id_to_index: dict[int, int] = {}
            main_ok_count = 0
            history_ok_count = 0
            next_id = self.HK_BASE_ID
            for source, punct, label, mods, vk, raw in main_hotkeys:
                if not vk:
                    continue
                hotkey_id = next_id
                next_id += 1
                if user32.RegisterHotKey(None, hotkey_id, mods | 0x4000, vk):
                    registered_ids.append(hotkey_id)
                    main_id_to_hotkey[hotkey_id] = (source, punct, label)
                    main_ok_count += 1
                else:
                    safe_print(f"{log_prefix('[main]', now_str())}❌ Win32 主快捷鍵註冊失敗: {raw} (mods=0x{mods:X} vk=0x{vk:X})")
            for i, (mods, vk) in enumerate(parsed):
                if not vk:
                    continue
                hotkey_id = next_id
                next_id += 1
                if user32.RegisterHotKey(None, hotkey_id, mods | 0x4000, vk):
                    registered_ids.append(hotkey_id)
                    history_id_to_index[hotkey_id] = i
                    history_ok_count += 1
                else:
                    safe_print(f"{log_prefix('[main]', now_str())}❌ Win32 記憶快捷鍵 {i + 1} 註冊失敗 (mods=0x{mods:X} vk=0x{vk:X})")
            if main_hotkeys:
                safe_print(f"{log_prefix('[main]', now_str())}✅ 主快捷鍵 {main_ok_count}/{len(main_hotkeys)} 已註冊 (Win32)")
            safe_print(f"{log_prefix('[main]', now_str())}✅ 記憶快捷鍵 {history_ok_count}/5 已註冊 (Win32)")
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    hotkey_id = int(msg.wParam)
                    if hotkey_id in main_id_to_hotkey:
                        _source, punct, label = main_id_to_hotkey[hotkey_id]
                        t = time.perf_counter()
                        safe_print(
                            f"{log_prefix('[main]', now_str())}⌨️ 主快捷鍵觸發（Win32/{label}），"
                            f"keys={self.input.modifier_state_summary()}"
                        )
                        self.toggle_requested.emit(punct)
                        safe_print(f"{log_prefix('[main]', now_str())}⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                    elif hotkey_id in history_id_to_index:
                        idx = history_id_to_index[hotkey_id]
                        safe_print(
                            f"{log_prefix('[main]', now_str())}⌨️ 記憶快捷鍵 {idx + 1} 觸發，"
                            f"keys={self.input.modifier_state_summary()}"
                        )
                        self.history_requested.emit(int(idx))
            for hotkey_id in registered_ids:
                user32.UnregisterHotKey(None, hotkey_id)

        self._hk_thread = threading.Thread(target=_listener, daemon=True, name="HotkeyListener")
        self._hk_thread.start()

    def shutdown(self) -> None:
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self._stop_win32_hotkeys()
