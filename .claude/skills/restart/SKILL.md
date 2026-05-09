---
name: restart
description: 重啟 AI Whisper Next。收到「重啟」、「restart」、「重新啟動」時觸發，或修改完 .py 程式碼後自動執行。
---

# AI Whisper Next 重啟流程

## 執行重啟

**永遠只使用 `restart-with-log.ps1`**，不要直接執行 `py run_ai_whisper.py`。

```powershell
powershell -ExecutionPolicy Bypass -File ".cursor/skills/restart/restart-with-log.ps1"
```

執行時設定 timeout 15000ms 等待腳本完成（腳本內含 4 秒等待 + 讀 log）。

working_directory 指向 workspace 根目錄（不要寫死磁碟代號，家裡 F:\、公司 D:\）。

## 確認啟動成功

確認輸出中有出現啟動訊息（如 `[main]` 開頭的 log 行）。

**若 log 為空：不代表啟動失敗**，只是程序還沒輸出，不要額外再執行 `py run_ai_whisper.py`。

## 觸發時機

以下兩種情況都必須執行重啟：

1. 每次修改完 .py 程式碼後，必須自動重啟 AI Whisper Next 讓改動生效，不需要等使用者要求。
2. 使用者傳送「重啟」、「restart」、「重新啟動」，立即執行重啟。

## LOG 監控

- log 寫入 `ai_whisper_yyyyMMdd_HHmmss.log`（workspace 根目錄）
- 若使用者說「幫我看 LOG」、「看 log」，讀取最新的 `ai_whisper_*.log` 檔

## 注意事項

- 使用 PowerShell，不使用 `&&`
- 不要直接執行 `py run_ai_whisper.py`，一律透過 `restart-with-log.ps1`
- 入口程式是 `run_ai_whisper.py`（非 `main.py`）
