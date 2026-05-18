from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(s: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", s)


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
        clean = _strip_ansi(s)
        try:
            self._log.write(clean)
        except Exception:
            pass
        if self._tap:
            try:
                self._buf += clean
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


def _retire_current_logs(directory: Path) -> None:
    """把目錄裡所有 *.current.log rename 成 *.log（去掉 .current 標記）。"""
    try:
        for p in directory.glob("*.current.log"):
            try:
                p.rename(p.with_name(p.name.replace(".current.log", ".log")))
            except Exception:
                pass
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
        _retire_current_logs(log_dir)
        log_path = log_dir / f"ai_whisper_{ts}.current.log"
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)

        tap_file = None
        if tap_dir is not None:
            tap_dir.mkdir(parents=True, exist_ok=True)
            _retire_current_logs(tap_dir)
            tap_path = tap_dir / f"{ts}.current.log"
            tap_file = open(tap_path, "w", encoding="utf-8", buffering=1)

        sys.stdout = _Tee(sys.stdout, log_file, tap_file)
        sys.stderr = _Tee(sys.stderr, log_file)
        safe_print(f"[main] LOG -> {log_path}")
        return log_path
    except Exception:
        return None
