---
name: restart
description: 重啟 AI Whisper Next。收到「重啟」、「restart」、「重新啟動」時觸發，或修改完 .py 程式碼後自動執行。
---

# AI Whisper Next 重啟流程

## 執行重啟

**永遠只使用 `restart-with-log.ps1`**，不要直接執行 `py run_ai_whisper.py`。
腳本內固定使用 `py -3.12 -u run_ai_whisper.py`，因為本專案的 PySide6 依賴安裝在 Python 3.12。

```powershell
powershell -ExecutionPolicy Bypass -File ".cursor/skills/restart/restart-with-log.ps1"
```

執行時設定 timeout 15000ms 等待腳本完成（腳本內含 4 秒等待 + 讀 log）。

working_directory 指向 workspace 根目錄（不要寫死磁碟代號，家裡 F:\、公司 D:\）。

## 對話輸出規則

這個 skill 的中間驗證只做不說。

若需要在工具執行前回覆，只能說：

`我來重啟。`

不要描述 skill 讀取、腳本狀態、輸出摘要、log 狀態、備援確認路徑、或正在查程序。

成功時最終回覆固定格式：

`已重啟。PID: <pid>，最新 log: <path>`

禁止輸出任何關於以下主題的旁白或近似改寫：

- 使用了哪個 skill
- 正在讀取 skill 檔案
- 腳本執行狀態
- 腳本輸出是否完整
- 內部補查程序或 log 的方式
- 啟動驗證的推理過程

只有真的無法確認啟動時，才簡短說明查到的異常。

## 確認啟動成功

重啟腳本結束後，內部確認 `logs/` 下最新 `ai_whisper_*.log`（含執行中的 `*.current.log`）或正在跑的 `run_ai_whisper.py` 程序。

不要額外再執行 `py run_ai_whisper.py`。

重啟成功後，**必須**讀取並呼叫 `verify` Skill 來對修改的模組進行功能測試與驗證。

## 觸發時機

以下兩種情況都必須執行重啟：

1. 每次修改完 .py 程式碼後，必須自動重啟 AI Whisper Next 讓改動生效，不需要等使用者要求。
2. 使用者傳送「重啟」、「restart」、「重新啟動」，立即執行重啟。

## LOG 監控

- 主程式 log 在專案根目錄下 `logs/`，檔名：`ai_whisper_yyyyMMdd_HHmmss_{hostname}.current.log`（見 **`CLAUDE.md`**「Log 位置」、「`hostname`」與 `_sanitize_hostname`）；下次啟動僅退役本機後綴的 `.current`。篩選 `ai_whisper_*.log` 仍涵蓋 `.current.log`。
- 若使用者說「幫我看 LOG」、「看 log」，讀取 `logs/` 內最新修改的 `ai_whisper_*.log` 檔。

## 注意事項

- 使用 PowerShell，不使用 `&&`
- 不要直接執行 `py run_ai_whisper.py`，一律透過 `restart-with-log.ps1`
- 若需手動檢查啟動指令，必須使用 `py -3.12 -u run_ai_whisper.py`，不要使用裸 `py`
- 入口程式是 `run_ai_whisper.py`（非 `main.py`）
