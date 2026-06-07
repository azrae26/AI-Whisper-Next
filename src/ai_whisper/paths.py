from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parents[1]


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
    return base_dir() / "logs"


def tap_log_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        # exe is at <project>/dist/AI Whisper/ → parents[1] = project root
        return Path(sys.executable).resolve().parents[2] / "tap_test_logs"
    return PROJECT_DIR / "tap_test_logs"



def ensure_runtime_dirs() -> None:
    os.makedirs(base_dir(), exist_ok=True)
