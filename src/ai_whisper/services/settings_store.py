from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from ..logging_setup import log_prefix, now_str, safe_print
from ..models import AppConfig, TextCorrection
from ..paths import config_file


def _float_value(value, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config_file()
        self._lock = threading.Lock()
        self._config = self.load()

    def get(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = self._from_dict(data)
            safe_print(f"{log_prefix('[settings]', now_str())}config loaded from {self.path}")
            return cfg
        except Exception as e:
            safe_print(f"{log_prefix('[settings]', now_str())}⚠️ 讀取設定失敗 {self.path}: {e}")
            return AppConfig()

    def save(self, updates: dict | AppConfig) -> AppConfig:
        # ⚠️ 修復 5：加鎖防止併發寫入互相覆蓋。
        # 鎖內重新從磁碟讀取最新狀態再 merge，確保不丟失其他 thread 剛寫入的資料。
        with self._lock:
            if isinstance(updates, AppConfig):
                new_config = updates
            else:
                # 從磁碟重新讀取最新狀態
                if self.path.exists():
                    try:
                        with open(self.path, "r", encoding="utf-8") as f:
                            disk_data = json.load(f)
                        self._config = self._from_dict(disk_data)
                    except Exception:
                        pass  # 讀取失敗時用記憶體中的版本
                merged = self.to_dict(self._config)
                merged.update(updates)
                new_config = self._from_dict(merged)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.to_dict(new_config)
            fd, tmp = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            self._config = new_config
            return self._config

    @staticmethod
    def to_dict(cfg: AppConfig) -> dict:
        data = asdict(cfg)
        data["text_corrections"] = [
            {"from": item.source, "to": item.target}
            for item in cfg.text_corrections
        ]
        return data

    @classmethod
    def _from_dict(cls, data: dict) -> AppConfig:
        defaults = AppConfig()
        corrections = []
        for item in data.get("text_corrections", []) or []:
            if isinstance(item, dict) and item.get("from"):
                corrections.append(TextCorrection(str(item.get("from", "")), str(item.get("to", ""))))
        history = data.get("history_hotkeys", defaults.history_hotkeys)
        if not isinstance(history, list):
            history = defaults.history_hotkeys
        history = [str(x).strip().lower() for x in history[:5]]
        while len(history) < 5:
            history.append(defaults.history_hotkeys[len(history)])
        return AppConfig(
            apiKey=str(data.get("apiKey", defaults.apiKey)),
            hotkey=str(data.get("hotkey", defaults.hotkey)).strip().lower(),
            hotkey_comma=str(data.get("hotkey_comma", defaults.hotkey_comma)).strip().lower(),
            history_hotkeys=history,
            model=str(data.get("model", defaults.model)),
            startup=bool(data.get("startup", defaults.startup)),
            geometry=str(data.get("geometry", defaults.geometry)),
            text_corrections=corrections,
            segment_silence=_float_value(data.get("segment_silence"), defaults.segment_silence),
            segment_max_accum=_float_value(data.get("segment_max_accum"), defaults.segment_max_accum),
            segment_short_silence=_float_value(data.get("segment_short_silence"), defaults.segment_short_silence),
            warmup_idle_minutes=_float_value(data.get("warmup_idle_minutes"), defaults.warmup_idle_minutes),
            vad_confidence=_float_value(data.get("vad_confidence"), defaults.vad_confidence),
            vad_min_speech_sec=_float_value(data.get("vad_min_speech_sec"), defaults.vad_min_speech_sec),
            tap_trigger_enabled=bool(data.get("tap_trigger_enabled", defaults.tap_trigger_enabled)),
            tap_sensitivity=_float_value(data.get("tap_sensitivity"), defaults.tap_sensitivity),
            overlay_positions={
                k: v for k, v in (data.get("overlay_positions") or {}).items()
                if isinstance(v, dict) and "x" in v and "y" in v
            },
        )


def set_startup(enabled: bool) -> None:
    import sys
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = f'"{sys.executable}"'
                if not getattr(sys, "_MEIPASS", None):
                    cmd = f'"{sys.executable}" -m ai_whisper'
                winreg.SetValueEx(key, "AIWhisper", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "AIWhisper")
                except FileNotFoundError:
                    pass
    except Exception as e:
        safe_print(f"{log_prefix('[settings][set_startup]', now_str())}❌ 錯誤: {e}")


def is_startup_enabled() -> bool:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, "AIWhisper")
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

