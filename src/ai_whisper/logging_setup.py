from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path


class _Tee:
    def __init__(self, original, log_file, tap_file=None):
        self._orig = original
        self._log = log_file
        self._tap = tap_file
        self._buf = ""

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
        if self._tap:
            try:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if "[tap]" in line:
                        self._tap.write(line + "\n")
                        self._tap.flush()
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
    return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


def install_log_tee(log_dir: Path, tap_dir: Path | None = None) -> Path | None:
    try:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"ai_whisper_{ts}.log"
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)

        tap_file = None
        if tap_dir is not None:
            tap_dir.mkdir(parents=True, exist_ok=True)
            tap_path = tap_dir / f"{ts}.log"
            tap_file = open(tap_path, "w", encoding="utf-8", buffering=1)

        sys.stdout = _Tee(sys.stdout, log_file, tap_file)
        sys.stderr = _Tee(sys.stderr, log_file)
        safe_print(f"[main] LOG -> {log_path}")
        return log_path
    except Exception:
        return None

