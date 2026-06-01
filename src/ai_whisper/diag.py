from __future__ import annotations

"""診斷啟動旗標（暫時性除錯用）。

用環境變數在啟動時「個別關閉」可疑元件，配合 LatencyMon 以二分法定位
「害系統殘留卡頓（關程式後仍在、重開機才好）」的觸發點。預設全部啟用，
未設任何旗標時行為與正常版完全相同。

用法（PowerShell，單次啟動；每測一輪前請先重開機清掉殘留狀態）：

    # 例：把全域熱鍵鉤子與 VAD 預載關掉再啟動
    $env:AIW_DIAG_NO_HOTKEY=1; $env:AIW_DIAG_NO_VAD=1
    python -m ai_whisper.app
    # 測完關閉環境變數：Remove-Item Env:AIW_DIAG_NO_HOTKEY, Env:AIW_DIAG_NO_VAD

旗標：
    AIW_DIAG_NO_VAD      跳過 Silero VAD / torch 預載
    AIW_DIAG_NO_NETWARM  跳過 OpenAI 連線預熱
    AIW_DIAG_NO_HOTKEY   跳過全域熱鍵註冊（RegisterHotKey + keyboard 低階鉤子）
    AIW_DIAG_NO_TAP      強制關閉敲麥偵測（不論 config 設定）

值為 "1"/"true"/任何非空非零字串即視為「關閉該元件」；空字串、"0"、"false" 視為不關。
定位到元凶後本模組與相關 gate 應一併移除。
"""

import os

# component key -> 對應環境變數名
_FLAGS = {
    "vad": "AIW_DIAG_NO_VAD",
    "netwarm": "AIW_DIAG_NO_NETWARM",
    "hotkey": "AIW_DIAG_NO_HOTKEY",
    "tap": "AIW_DIAG_NO_TAP",
}


def is_disabled(component: str) -> bool:
    """該診斷元件是否被環境變數要求關閉。"""
    env = _FLAGS.get(component)
    if not env:
        return False
    val = os.environ.get(env, "").strip().lower()
    return val not in ("", "0", "false")


def active_summary() -> str:
    """回傳目前被關閉的元件清單字串，供啟動時 log 確認。"""
    off = [name for name in _FLAGS if is_disabled(name)]
    return ",".join(off) if off else "(none)"
