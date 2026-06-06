"""AI Whisper Next Debug Query CLI.

Connects to the Debug Inspection Server (127.0.0.1:47643) and sends JSON queries.
Zero dependencies beyond Python stdlib.

Usage:
    py scripts/debug_query.py ping
    py scripts/debug_query.py config
    py scripts/debug_query.py ui_tree [--depth N]
    py scripts/debug_query.py eval "self.state"
    py scripts/debug_query.py eval "self.toggle_recording()"
    py scripts/debug_query.py eval "self.window.show_settings()"
    py scripts/debug_query.py eval "bool(self.cfg.apiKey)"
"""
from __future__ import annotations

import json
import socket
import sys

HOST = "127.0.0.1"
PORT = 47643


def query(method: str, params: dict | None = None) -> dict:
    req: dict = {"method": method}
    if params:
        req["params"] = params
    try:
        with socket.create_connection((HOST, PORT), timeout=3) as s:
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return json.loads(buf.decode("utf-8").strip())
    except ConnectionRefusedError:
        return {"ok": False, "error": "App not running (connection refused on 127.0.0.1:47643)"}
    except TimeoutError:
        return {"ok": False, "error": "Connection timed out"}
    except OSError as e:
        return {"ok": False, "error": f"Connection failed: {e}"}


def main():
    if len(sys.argv) < 2:
        print("AI Whisper Next — Debug Query CLI")
        print()
        print("Usage: py scripts/debug_query.py <method> [args]")
        print()
        print("Methods:")
        print("  ping                          Check if app is running")
        print("  config                        Show current config (API key masked)")
        print("  ui_tree [--depth N]           Show widget tree (default depth=8)")
        print('  eval "<expression>"           Run Python expression (self=controller)')
        print()
        print("Eval examples:")
        print('  eval "self.state"                           → current state')
        print('  eval "self.cfg.hotkey"                      → hotkey setting')
        print('  eval "self.audio._stream is not None"       → mic stream active?')
        print('  eval "self.toggle_recording()"              → trigger recording')
        print('  eval "self.window.show_settings()"          → open settings')
        print('  eval "self.window.show_from_tray()"         → show window')
        print('  eval "self.quit_app()"                      → quit app')
        sys.exit(1)

    method = sys.argv[1]
    params: dict = {}

    if method == "eval":
        expr = " ".join(sys.argv[2:])
        if not expr:
            print("Error: eval requires an expression", file=sys.stderr)
            sys.exit(1)
        params["expr"] = expr
    elif method == "ui_tree" and "--depth" in sys.argv:
        idx = sys.argv.index("--depth")
        if idx + 1 < len(sys.argv):
            params["max_depth"] = int(sys.argv[idx + 1])

    result = query(method, params if params else None)
    out = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
