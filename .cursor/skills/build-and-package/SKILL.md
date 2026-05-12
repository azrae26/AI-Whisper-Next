---
name: build-and-package
description: 打包 AI Whisper Next 並壓縮成 zip 準備分發。當使用者說「包」、「打包」、「build」、「壓縮」、「產生 zip」、「分享給同事」時使用。
---

# AI Whisper Next 打包流程

使用專案根目錄下的**打包專用 venv**（目錄名由 `scripts/deploy.ps1` 的 `$packVenvDirName` 決定），避免大型套件進入日常開發用的 `.venv/`。
打包邏輯完整實作在 `scripts/deploy.ps1`，本文件說明使用方式與注意事項。

## 執行方式

### 只打包（不推 git）—— 說「包」時用這個

```powershell
powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1"
```

Bash tool timeout：build 步驟 300000ms，zip 步驟 60000ms。

### 只 build（不 zip）

```powershell
powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1" -BuildOnly
```

### build + 等待 zip 完成

```powershell
powershell -ExecutionPolicy Bypass -File "scripts/pack.ps1" -WaitZip
```

## 完成後告知使用者

- zip 路徑：`dist/AI Whisper_yyyyMMdd_HHmm.zip`
- `dist/` 底下的 zip 只保留最近 3 個，其餘自動刪除
- 傳給同事，解壓後直接執行 `AI Whisper.exe`
- 首次執行需在設定頁輸入 API Key

## 注意事項

- 打包腳本路徑自動解析（`$PSScriptRoot`），不需手動改路徑
- 打包前自動備份 `dist/AI Whisper/config.json`，完成後還原，不會遺失使用者設定
- 壓縮優先使用系統 tar.exe，否則 fallback 至 Compress-Archive（staging 至 TEMP）
- 打包用 venv 的資料夾名以 `deploy.ps1` 的 `$packVenvDirName` 為準，該類目錄已列入 `.gitignore`；第一次執行時若不存在會自動建立
- 多台電腦共用同一專案目錄時，同步過來的 venv 可能指向別台 Python 路徑而無法執行：`deploy.ps1` 會偵測並自動刪除重建（優先 `py`，否則 `python`）
- build 完成後舊程式自動終止，新 exe 自動啟動
