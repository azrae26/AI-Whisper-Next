"""Debug Inspection Server — 桌面版 DevTools for AI Whisper Next.

QTcpServer on 127.0.0.1:47643, accepts JSON-over-TCP queries.
Zero maintenance: eval can query/call anything, ui_tree auto-walks widgets,
config auto-includes all fields via dataclasses.asdict.

Protocol: newline-delimited JSON (one request → one response → disconnect)
  → {"method": "ping"}\n
  ← {"ok": true, "result": {"pong": true, "pid": 1234}}\n

Methods:
  ping     — health check (pid, uptime)
  config   — full AppConfig (API key masked)
  ui_tree  — recursive widget hierarchy (like DOM snapshot)
  eval     — run arbitrary Python expression; self = controller
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import QWidget

from ..logging_setup import log_prefix, now_str, safe_print

DEBUG_PORT = 47643


class DebugServer(QObject):
    """Lightweight debug inspection server — the 'DevTools' for AI Whisper.

    Once started, never needs code changes regardless of app feature changes:
    - eval: arbitrary Python expression (self = controller) — like evaluate_script
    - ui_tree: auto-walks any widget hierarchy — like DOM snapshot
    - config: dataclasses.asdict auto-picks up all fields
    """

    def __init__(self, controller):
        super().__init__()
        self._controller = controller
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._start_time = time.time()

    def start(self) -> None:
        if self._server.listen(QHostAddress("127.0.0.1"), DEBUG_PORT):
            safe_print(f"{log_prefix('[debug]', now_str())}🔍 Debug server listening on 127.0.0.1:{DEBUG_PORT}")
        else:
            safe_print(f"{log_prefix('[debug]', now_str())}⚠️ Debug server failed: {self._server.errorString()}")

    def shutdown(self) -> None:
        if self._server.isListening():
            self._server.close()

    # ── connection handling ─────────────────────────────────

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if not sock:
                continue
            sock.readyRead.connect(lambda s=sock: self._on_data(s))
            sock.disconnected.connect(sock.deleteLater)

    def _on_data(self, sock) -> None:
        if not sock.canReadLine():
            return
        raw = bytes(sock.readLine()).decode("utf-8", errors="replace").strip()
        if not raw:
            sock.disconnectFromHost()
            return
        try:
            req = json.loads(raw)
            method = req.get("method", "")
            params = req.get("params", {}) or {}
            result = self._dispatch(method, params)
            resp = {"ok": True, "result": result}
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        out = json.dumps(resp, ensure_ascii=False, default=str) + "\n"
        sock.write(out.encode("utf-8"))
        sock.flush()
        sock.disconnectFromHost()

    # ── method dispatch ─────────────────────────────────────

    def _dispatch(self, method: str, params: dict):
        handlers = {
            "ping": self._h_ping,
            "config": self._h_config,
            "ui_tree": self._h_ui_tree,
            "eval": self._h_eval,
        }
        handler = handlers.get(method)
        if handler is None:
            raise ValueError(
                f"unknown method: {method}. Available: {list(handlers.keys())}"
            )
        return handler(params)

    # ── handlers ────────────────────────────────────────────

    def _h_ping(self, _p):
        return {
            "pong": True,
            "pid": os.getpid(),
            "uptime_sec": round(time.time() - self._start_time, 1),
        }

    def _h_config(self, _p):
        d = asdict(self._controller.cfg)
        key = d.get("apiKey", "")
        d["apiKey"] = (key[:5] + "***") if key else ""
        return d

    def _h_ui_tree(self, params):
        max_depth = params.get("max_depth", 8)
        return _widget_to_dict(self._controller.window, max_depth=max_depth)

    def _h_eval(self, params):
        expr = params.get("expr", "")
        if not expr:
            raise ValueError("missing 'expr' parameter")
        # 'self' in the expression refers to the controller,
        # just like Chrome DevTools' evaluate_script has 'document'.
        return eval(expr, {"__builtins__": __builtins__}, {"self": self._controller})


# ── ui tree helper ──────────────────────────────────────────


def _widget_to_dict(widget, depth: int = 0, max_depth: int = 8) -> dict:
    """Recursively convert a QWidget hierarchy to a dict (like DOM snapshot)."""
    if depth >= max_depth:
        return {"type": type(widget).__name__, "_truncated": True}

    info: dict = {"type": type(widget).__name__}

    name = widget.objectName()
    if name:
        info["name"] = name

    if not widget.isVisible():
        info["visible"] = False

    # text content — try common Qt text accessors
    for attr in ("text", "currentText", "windowTitle"):
        fn = getattr(widget, attr, None)
        if fn and callable(fn):
            try:
                val = fn()
                if val and isinstance(val, str) and len(val) < 200:
                    info[attr] = val
                    break
            except Exception:
                pass

    # checked state (QCheckBox, QRadioButton, etc.)
    fn = getattr(widget, "isChecked", None)
    if fn and callable(fn):
        try:
            info["checked"] = fn()
        except Exception:
            pass

    # recurse into children
    children = [
        _widget_to_dict(c, depth + 1, max_depth)
        for c in widget.children()
        if isinstance(c, QWidget)
    ]
    if children:
        info["children"] = children

    return info
