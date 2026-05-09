from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path


class _Tee:
    def __init__(self, original, log_file):
        self._orig = original
        self._log = log_file

    def write(self, s):
        try:
            if self._orig:
                self._orig.write(s)
        except Exception:
            pass
        try:
            self._log.write(s)
        except Exception:
            pass

    def flush(self):
        try:
            if self._orig:
                self._orig.flush()
        except Exception:
            pass
        try:
            self._log.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._orig, name)


def now_str() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


def install_log_tee(log_dir: Path) -> Path | None:
    try:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f'ai_whisper_{_dt.datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(sys.stdout, log_file)
        sys.stderr = _Tee(sys.stderr, log_file)
        safe_print(f"[main] LOG -> {log_path}")
        return log_path
    except Exception:
        return None

