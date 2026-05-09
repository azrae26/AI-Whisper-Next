from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from ..logging_setup import safe_print
from ..models import AppConfig, TextCorrection
from ..paths import config_file, legacy_config_candidates


def _float_value(value, fallback: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config_file()
        self._loaded_from: Path | None = None
        self._config = self.load()

    @property
    def loaded_from(self) -> Path | None:
        return self._loaded_from

    def get(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        candidates = [self.path, *legacy_config_candidates()]
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._loaded_from = candidate
                cfg = self._from_dict(data)
                safe_print(f"[settings] config loaded from {candidate}")
                return cfg
            except Exception as e:
                safe_print(f"[settings] ⚠️ 讀取設定失敗 {candidate}: {e}")
        self._loaded_from = None
        return AppConfig()

    def save(self, updates: dict | AppConfig) -> AppConfig:
        if isinstance(updates, AppConfig):
            new_config = updates
        else:
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
        self._loaded_from = self.path
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
        )


def set_startup(enabled: bool) -> None:
    import sys
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
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
        winreg.CloseKey(key)
    except Exception as e:
        safe_print(f"[settings][set_startup] ❌ 錯誤: {e}")


def is_startup_enabled() -> bool:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.QueryValueEx(key, "AIWhisper")
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

