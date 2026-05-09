from __future__ import annotations

import ctypes
import re
import sys

from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from .controller import AppController
from .logging_setup import install_log_tee
from .paths import asset_dir, ensure_runtime_dirs, log_dir
from .services.settings_store import SettingsStore
from .ui.main_window import MainWindow


class CompactPasswordStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_LineEdit_PasswordCharacter:
            return 0x2022
        return super().styleHint(hint, option, widget, returnData)


def _apply_geometry(window: MainWindow, geometry: str) -> None:
    match = re.match(r"(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?", geometry or "")
    if not match:
        return
    w, h = int(match.group(1)), int(match.group(2))
    window.resize(w, h)
    if match.group(3) is not None:
        window.move(int(match.group(3)), int(match.group(4)))


def main() -> int:
    ensure_runtime_dirs()
    install_log_tee(log_dir())
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("AI Whisper")
    app.setStyle(CompactPasswordStyle(app.style()))
    icon_path = asset_dir() / "icon.ico"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))

    settings = SettingsStore()
    cfg = settings.get()
    window = MainWindow(cfg)
    _apply_geometry(window, cfg.geometry)
    controller = AppController(window, settings)
    app.aboutToQuit.connect(controller.cleanup)
    window.show()
    return app.exec()
