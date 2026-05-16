from __future__ import annotations

import ctypes
import threading
import time

import keyboard

from ..logging_setup import now_str, safe_print

KEYEVENTF_KEYDOWN = 0
KEYEVENTF_KEYUP = 0x0002
VK_LCONTROL = 0xA2
VK_V = 0x56
CTRL_VKS = (0x11, 0xA2, 0xA3)  # generic Ctrl, left Ctrl, right Ctrl
CTRL_NAMES = {"ctrl", "control", "left ctrl", "right ctrl"}
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
PASTE_MODIFIER_VKS = (
    0xA0, 0xA1,  # left Shift, right Shift
    0xA2, 0xA3,  # left Ctrl, right Ctrl
    0xA4, 0xA5,  # left Alt, right Alt
    0x5B, 0x5C,  # left Windows, right Windows
)
MOD_NORMALIZE = {
    "left ctrl": "ctrl",
    "right ctrl": "ctrl",
    "left shift": "shift",
    "right shift": "shift",
    "left alt": "alt",
    "right alt": "alt",
    "left windows": "windows",
    "right windows": "windows",
}
MOD_RELEASE_VKS = {
    "ctrl": CTRL_VKS,
    "control": CTRL_VKS,
    "shift": (0x10, 0xA0, 0xA1),
    "alt": (0x12, 0xA4, 0xA5),
    "windows": (0x5B, 0x5C),
}


class InputService:
    def __init__(self) -> None:
        self._ctrl_guard_hook_remove = None
        self._ctrl_guard_lock = threading.Lock()
        self._ctrl_guard_down: set[str] = set()

    @staticmethod
    def vk_list(vks: list[int]) -> str:
        if not vks:
            return "none"
        return ",".join(VK_NAMES.get(vk, f"0x{vk:02X}") for vk in vks)

    @classmethod
    def modifier_state_summary(cls) -> str:
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
        return f"async_down={cls.vk_list(async_down)}; logical_down={cls.vk_list(logical_down)}"

    @staticmethod
    def is_ctrl_vk(vk: int) -> bool:
        return vk in CTRL_VKS

    def ctrl_state_down(self) -> bool:
        user32 = ctypes.windll.user32
        for vk in CTRL_VKS:
            try:
                if (user32.GetAsyncKeyState(vk) & 0x8000) or (user32.GetKeyState(vk) & 0x8000):
                    return True
            except Exception:
                continue
        return False

    def force_release_ctrl(self) -> None:
        user32 = ctypes.windll.user32
        for vk in CTRL_VKS:
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                continue

    def release_modifiers_for_paste(self) -> list[int]:
        user32 = ctypes.windll.user32
        released: list[int] = []
        for vk in PASTE_MODIFIER_VKS:
            try:
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                    released.append(vk)
            except Exception:
                continue
        return released

    def restore_modifiers(self, vks: list[int]) -> None:
        user32 = ctypes.windll.user32
        for vk in vks:
            try:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYDOWN, 0)
            except Exception:
                continue

    def send_ctrl_v(self) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_LCONTROL, 0, KEYEVENTF_KEYDOWN, 0)
        try:
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYDOWN, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        finally:
            self.force_release_ctrl()

    def schedule_hotkey_modifier_cleanup(self, mods: list[str], source: str) -> None:
        vks: list[int] = []
        for mod in mods:
            for vk in MOD_RELEASE_VKS.get(MOD_NORMALIZE.get(mod, mod), ()):
                if vk not in vks:
                    vks.append(vk)
        if not vks:
            return

        def _cleanup() -> None:
            user32 = ctypes.windll.user32
            down = []
            for vk in vks:
                try:
                    if user32.GetAsyncKeyState(vk) & 0x8000:
                        down.append(vk)
                except Exception:
                    continue
            if not down:
                return
            before = self.modifier_state_summary()
            for vk in down:
                try:
                    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                except Exception:
                    continue
            time.sleep(0.02)
            safe_print(
                f"[input][{now_str()}] ⌨️ 熱鍵修飾鍵清理({source}): "
                f"released={self.vk_list(down)}，before={before}，after={self.modifier_state_summary()}"
            )

        timer = threading.Timer(0.12, _cleanup)
        timer.daemon = True
        timer.start()

    def schedule_ctrl_cleanup(self, source: str) -> None:
        for delay in (0.08, 0.35):
            timer = threading.Timer(delay, self._release_stuck_ctrl_if_needed, args=(source,))
            timer.daemon = True
            timer.start()

    def _release_stuck_ctrl_if_needed(self, source: str) -> None:
        user32 = ctypes.windll.user32
        down: list[int] = []
        for vk in CTRL_VKS:
            try:
                if (user32.GetAsyncKeyState(vk) & 0x8000) or (user32.GetKeyState(vk) & 0x8000):
                    down.append(vk)
            except Exception:
                continue
        if not down:
            return
        before = self.modifier_state_summary()
        self.force_release_ctrl()
        time.sleep(0.02)
        safe_print(
            f"[input][{now_str()}] ⌨️ Ctrl 延遲清理({source}): "
            f"released={self.vk_list(down)}，before={before}，after={self.modifier_state_summary()}"
        )

    def start_ctrl_guard(self) -> None:
        with self._ctrl_guard_lock:
            if self._ctrl_guard_hook_remove:
                return
            self._ctrl_guard_down.clear()
        try:
            remove_hook = keyboard.hook(self._on_ctrl_guard_event, suppress=False)
        except Exception as e:
            safe_print(f"[input][{now_str()}] ⚠️ Ctrl狀態防護啟動失敗: {e}")
            return
        with self._ctrl_guard_lock:
            self._ctrl_guard_hook_remove = remove_hook
        safe_print(f"[input][{now_str()}] ✅ Ctrl狀態防護已啟動")

    def stop_ctrl_guard(self, removed_by_external_unhook: bool = False) -> None:
        with self._ctrl_guard_lock:
            remove_hook = self._ctrl_guard_hook_remove
            self._ctrl_guard_hook_remove = None
            self._ctrl_guard_down.clear()
        if remove_hook and not removed_by_external_unhook:
            try:
                remove_hook()
            except Exception:
                pass

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
            self._cleanup_stuck_ctrl_after_user_up,
            args=(name or "ctrl", scan_code, down_snapshot),
        )
        timer.daemon = True
        timer.start()

    @staticmethod
    def _is_ctrl_event(name: str, scan_code) -> bool:
        return name in CTRL_NAMES or scan_code in (29, 3613)

    @staticmethod
    def _ctrl_event_id(name: str, scan_code) -> str:
        if name in ("left ctrl", "right ctrl", "ctrl", "control"):
            return name
        return f"scan:{scan_code}"

    def _cleanup_stuck_ctrl_after_user_up(self, name: str, scan_code, down_snapshot: list[str]) -> None:
        with self._ctrl_guard_lock:
            if self._ctrl_guard_down:
                return
        if not self.ctrl_state_down():
            return
        before = self.modifier_state_summary()
        self.force_release_ctrl()
        time.sleep(0.02)
        safe_print(
            f"[input][{now_str()}] 🧹 Ctrl狀態防護清理: "
            f"after_up={name}/scan={scan_code}，tracked_down={down_snapshot or 'none'}，"
            f"before={before}，after={self.modifier_state_summary()}"
        )
