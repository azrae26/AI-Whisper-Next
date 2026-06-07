from __future__ import annotations

import ctypes
import threading
import time

import keyboard

from ..logging_setup import log_prefix, now_str, safe_print

KEYEVENTF_KEYDOWN = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_LCONTROL = 0xA2
VK_V = 0x56
CTRL_VKS = (0x11, 0xA2, 0xA3)  # generic Ctrl, left Ctrl, right Ctrl
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


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    )


class INPUTUNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (
        ("type", ctypes.c_ulong),
        ("u", INPUTUNION),
    )


class InputService:
    def __init__(self) -> None:
        self._mod_summary_cache: tuple[str, float] = ("", 0.0)
        self._hotkey_cleanup_timer: threading.Timer | None = None

    @staticmethod
    def vk_list(vks: list[int]) -> str:
        if not vks:
            return "none"
        return ",".join(VK_NAMES.get(vk, f"0x{vk:02X}") for vk in vks)

    def modifier_state_summary(self) -> str:
        now = time.perf_counter()
        cached_result, cached_time = self._mod_summary_cache
        if now - cached_time < 0.015:
            return cached_result
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
        result = f"async_down={self.vk_list(async_down)}; logical_down={self.vk_list(logical_down)}"
        self._mod_summary_cache = (result, now)
        return result

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

    def release_modifiers_for_paste(self, preserve_ctrl: bool = False) -> list[int]:
        user32 = ctypes.windll.user32
        released: list[int] = []
        for vk in PASTE_MODIFIER_VKS:
            if preserve_ctrl and self.is_ctrl_vk(vk):
                continue
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

    def send_v(self) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYDOWN, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)

    def send_unicode_text(self, text: str) -> bool:
        if not text:
            return True
        user32 = ctypes.windll.user32
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
        data = normalized.encode("utf-16-le", "surrogatepass")
        units = [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]
        # Batch send: 一次 SendInput 送出所有字元，避免逐字送出時
        # 某些應用（如 Chrome Omnibox）在字元之間觸發自動完成導致吞字。
        # 上限 100：Electron/Chrome 大批次（≥500）反而丟字更嚴重。
        BATCH = 100  # 每批最多 100 個字元（200 個 INPUT 事件）
        for start in range(0, len(units), BATCH):
            chunk = units[start : start + BATCH]
            n = len(chunk) * 2
            inputs = (INPUT * n)()
            for idx, unit in enumerate(chunk):
                inputs[idx * 2].type = INPUT_KEYBOARD
                inputs[idx * 2].ki.wScan = unit
                inputs[idx * 2].ki.dwFlags = KEYEVENTF_UNICODE
                inputs[idx * 2 + 1].type = INPUT_KEYBOARD
                inputs[idx * 2 + 1].ki.wScan = unit
                inputs[idx * 2 + 1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            sent = user32.SendInput(n, inputs, ctypes.sizeof(INPUT))
            if sent != n:
                return False
        return True

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
                f"{log_prefix('[input]', now_str())}⌨️ 熱鍵修飾鍵清理({source}): "
                f"released={self.vk_list(down)}，before={before}，after={self.modifier_state_summary()}"
            )

        if self._hotkey_cleanup_timer is not None:
            self._hotkey_cleanup_timer.cancel()
        timer = threading.Timer(0.12, _cleanup)
        timer.daemon = True
        timer.start()
        self._hotkey_cleanup_timer = timer

    def cleanup_ctrl_now(self, source: str) -> None:
        self._release_stuck_ctrl_if_needed(source)

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
            f"{log_prefix('[input]', now_str())}⌨️ Ctrl 延遲清理({source}): "
            f"released={self.vk_list(down)}，before={before}，after={self.modifier_state_summary()}"
        )
