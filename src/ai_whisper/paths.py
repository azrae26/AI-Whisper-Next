from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AI Whisper"
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parents[1]
OLD_PROJECT_DIR = Path(r"F:\Cursor\AI Whisper")


def base_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys.executable).resolve().parent
    return PROJECT_DIR


def asset_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(getattr(sys, "_MEIPASS")) / "assets"
    return PROJECT_DIR / "assets"


def config_file() -> Path:
    return base_dir() / "config.json"


def log_dir() -> Path:
    return base_dir()


def tap_log_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        # exe is at <project>/dist/AI Whisper/ → parents[1] = project root
        return Path(sys.executable).resolve().parents[2] / "tap_test_logs"
    return PROJECT_DIR / "tap_test_logs"


def legacy_config_candidates() -> list[Path]:
    return [
        OLD_PROJECT_DIR / "dist" / APP_NAME / "config.json",
        OLD_PROJECT_DIR / "config.json",
    ]


def ensure_runtime_dirs() -> None:
    os.makedirs(base_dir(), exist_ok=True)
