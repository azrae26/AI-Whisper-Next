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
KEYEVENTF_KEYUP = 0x0002
CTRL_DIAG_NAMES = {"ctrl", "control", "left ctrl", "right ctrl"}
CTRL_RELEASE_VKS = (0x11, 0xA2, 0xA3)
CTRL_STUCK_CHECK_DELAY_SEC = 0.08
KEY_STATE_VKS = (
    0x10, 0x11, 0x12,  # generic Shift, Ctrl, Alt
    0xA0, 0xA1,  # left Shift, right Shift
    0xA2, 0xA3,  # left Ctrl, right Ctrl
    0xA4, 0xA5,  # left Alt, right Alt
    0x5B, 0x5C,  # left Windows, right Windows
)
VK_NAMES = {
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0xA0: "LShift",
    0xA1: "RShift",
    0xA2: "LCtrl",
    0xA3: "RCtrl",
    0xA4: "LAlt",
    0xA5: "RAlt",
    0x5B: "LWin",
    0x5C: "RWin",
}
MOD_RELEASE_VKS = {
    "ctrl": (0x11, 0xA2, 0xA3),
    "control": (0x11, 0xA2, 0xA3),
    "shift": (0x10, 0xA0, 0xA1),
    "alt": (0x12, 0xA4, 0xA5),
    "windows": (0x5B, 0x5C),
}


def parse_hotkey(hk_str: str) -> tuple[list[str], str | None]:
    parts = [p.strip().lower() for p in hk_str.split("+") if p.strip()]
    mods = [p for p in parts if p in MODIFIERS]
    main_key = next((p for p in reversed(parts) if p not in MODIFIERS), None)
    return mods, main_key


def _vk_list(vks: list[int]) -> str:
    if not vks:
        return "none"
    return ",".join(VK_NAMES.get(vk, f"0x{vk:02X}") for vk in vks)


def modifier_state_summary() -> str:
    user32 = ctypes.windll.user32
    async_down: list[int] = []
    logical_down: list[int] = []
    for vk in KEY_STATE_VKS:
        try:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                async_down.append(vk)
            if user32.GetKeyState(vk) & 0x8000:
                logical_down.append(vk)
        except Exception:
            continue
    return f"async_down={_vk_list(async_down)}; logical_down={_vk_list(logical_down)}"


def _ctrl_state_down() -> bool:
    user32 = ctypes.windll.user32
    for vk in CTRL_RELEASE_VKS:
        try:
            if (user32.GetAsyncKeyState(vk) & 0x8000) or (user32.GetKeyState(vk) & 0x8000):
                return True
        except Exception:
            continue
    return False


def _force_release_ctrl_keys() -> None:
    user32 = ctypes.windll.user32
    for vk in CTRL_RELEASE_VKS:
        try:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            continue


def schedule_hotkey_modifier_cleanup(mods: list[str], source: str) -> None:
    vks: list[int] = []
    for mod in mods:
        for vk in MOD_RELEASE_VKS.get(MOD_NORMALIZE.get(mod, mod), ()):
            if vk not in vks:
                vks.append(vk)
    if not vks:
        return

    def _cleanup() -> None:
        user32 = ctypes.windll.user32
        down = [vk for vk in vks if user32.GetAsyncKeyState(vk) & 0x8000]
        if not down:
            return
        before = modifier_state_summary()
        for vk in down:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        safe_print(
            f"[main][{now_str()}] ⌨️ 熱鍵修飾鍵清理({source}): "
            f"released={_vk_list(down)}，before={before}，after={modifier_state_summary()}"
        )

    threading.Timer(0.12, _cleanup).start()


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
        self._ctrl_guard_hook_remove = None
        self._ctrl_guard_lock = threading.Lock()
        self._ctrl_guard_down: set[str] = set()

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
                    safe_print(
                        f"[main][{now_str()}] ⌨️ 熱鍵觸發，排入 after(0)，"
                        f"keys={modifier_state_summary()}"
                    )
                    self.toggle_requested.emit(p)
                    schedule_hotkey_modifier_cleanup(hk_mods, "main")
                    safe_print(f"[main][{now_str()}] ⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                keyboard.add_hotkey(hotkey, _hk_fired)

            if hc_main in NAME_HOOK_KEYS:
                name_triggers.append((hc_mods, hc_main, "，"))
            else:
                def _hk_comma_fired(p="，"):
                    t = time.perf_counter()
                    safe_print(
                        f"[main][{now_str()}] ⌨️ 熱鍵觸發，排入 after(0)，"
                        f"keys={modifier_state_summary()}"
                    )
                    self.toggle_requested.emit(p)
                    schedule_hotkey_modifier_cleanup(hc_mods, "comma")
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
                            safe_print(
                                f"[main][{now_str()}] ⌨️ 熱鍵觸發（name hook），排入 after(0)，"
                                f"keys={modifier_state_summary()}"
                            )
                            self.toggle_requested.emit(punct)
                            schedule_hotkey_modifier_cleanup(mods, "name")
                            safe_print(f"[main][{now_str()}] ⌨️ after(0) 執行延遲 {(time.perf_counter() - t) * 1000:.1f}ms")
                            break
                self._comma_hook_remove = keyboard.hook(_on_name_hook)

            safe_print(f"[main][{now_str()}] ✅ 快捷鍵 {hotkey}（句號）、{hotkey_comma}（逗號）已註冊")
        except Exception as e:
            safe_print(f"[main][{now_str()}] ❌ 快捷鍵註冊失敗: {e}")

        self._ensure_ctrl_state_guard()
        self.register_history_hotkeys(history_hotkeys)

    def _ensure_ctrl_state_guard(self) -> None:
        with self._ctrl_guard_lock:
            if self._ctrl_guard_hook_remove:
                return
            self._ctrl_guard_down.clear()
        try:
            remove_hook = keyboard.hook(self._on_ctrl_guard_event, suppress=False)
        except Exception as e:
            safe_print(f"[main][{now_str()}] ⚠️ Ctrl狀態防護啟動失敗: {e}")
            return
        with self._ctrl_guard_lock:
            self._ctrl_guard_hook_remove = remove_hook
        safe_print(f"[main][{now_str()}] ✅ Ctrl狀態防護已啟動")

    def _on_ctrl_guard_event(self, event) -> None:
        name = (getattr(event, "name", "") or "").lower()
        scan_code = getattr(event, "scan_code", None)
        event_type = getattr(event, "event_type", "")
        if not self._is_ctrl_event(name, scan_code):
            return
        key_id = self._ctrl_event_id(name, scan_code)
        with self._ctrl_guard_lock:
            if event_type == keyboard.KEY_DOWN:
                self._ctrl_guard_down.add(key_id)
            elif event_type == keyboard.KEY_UP:
                self._ctrl_guard_down.discard(key_id)
            down_snapshot = sorted(self._ctrl_guard_down)
        if event_type != keyboard.KEY_UP:
            return
        timer = threading.Timer(
            CTRL_STUCK_CHECK_DELAY_SEC,
            self._cleanup_stuck_ctrl_if_needed,
            args=(name or "ctrl", scan_code, down_snapshot),
        )
        timer.daemon = True
        timer.start()

    @staticmethod
    def _is_ctrl_event(name: str, scan_code) -> bool:
        return name in CTRL_DIAG_NAMES or scan_code in (29, 3613)

    @staticmethod
    def _ctrl_event_id(name: str, scan_code) -> str:
        if name in ("left ctrl", "right ctrl", "ctrl", "control"):
            return name
        return f"scan:{scan_code}"

    def _cleanup_stuck_ctrl_if_needed(self, name: str, scan_code, down_snapshot: list[str]) -> None:
        with self._ctrl_guard_lock:
            if self._ctrl_guard_down:
                return
        if not _ctrl_state_down():
            return
        before = modifier_state_summary()
        _force_release_ctrl_keys()
        time.sleep(0.02)
        safe_print(
            f"[main][{now_str()}] 🧹 Ctrl狀態防護清理: "
            f"after_up={name}/scan={scan_code}，tracked_down={down_snapshot or 'none'}，"
            f"before={before}，after={modifier_state_summary()}"
        )

    def start_capture(self) -> None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        with self._ctrl_guard_lock:
            self._ctrl_guard_hook_remove = None
            self._ctrl_guard_down.clear()
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
                        safe_print(
                            f"[main][{now_str()}] ⌨️ 記憶快捷鍵 {idx + 1} 觸發，"
                            f"keys={modifier_state_summary()}"
                        )
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
