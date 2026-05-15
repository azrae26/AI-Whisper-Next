---
name: build-and-package
description: 只打包 AI Whisper Next 並產生 zip，不做 git add/commit/pull/push。Use ONLY when the user asks for「包」「打包」「build」「壓縮」「產生 zip」「分享給同事」and the request does NOT include「推」「推送」「push」「commit」「git」「推包」「推+包」. If the request includes both push/git intent and package/build intent, use push-and-package instead.
---

# AI Whisper Next 打包流程

使用專案根目錄下的**打包專用 venv**，避免大型套件進入日常開發用的 `.venv/`。
venv 名稱由 `scripts/deploy.ps1` 依電腦判斷：
- 家裡電腦 `P8-32`：`.venv-pack`
- 公司電腦（其他電腦預設）：`.venv-pack_office`
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

## 與 push-and-package（推包）的關係

- 使用者同一則需求為「推包」時，依 **push-and-package** skill：**同一輪禁止**在已背景啟動 `pack.ps1`（不帶參數）後，再為「確認 zip／timestamp 仍是舊的」而執行 `-WaitZip` 或第二次無參數 `pack.ps1`；應輪詢 `dist` 或請使用者稍後查看。
- 「只包」時仍可依本 skill 單獨執行 `pack.ps1`／`-WaitZip`；勿與同一 `dist` 上已在跑的另一個 PyInstaller／`pack` 行程硬撐並行。

## 注意事項

- 打包腳本路徑自動解析（`$PSScriptRoot`），不需手動改路徑
- 打包前自動備份 `dist/AI Whisper/config.json`，完成後還原，不會遺失使用者設定
- 壓縮優先使用系統 tar.exe，否則 fallback 至 Compress-Archive（staging 至 TEMP）
- 打包用 venv 由 `deploy.ps1` 的 `Get-PackVenvDirName` 依 `$env:COMPUTERNAME` 決定；家裡 `P8-32` 用 `.venv-pack`，公司機用 `.venv-pack_office`
- 不要人工猜測或硬改 venv 名稱；先看 `$env:COMPUTERNAME` 與 `deploy.ps1` 的 mapping
- 多台電腦共用同一專案目錄時，同步過來的 venv 可能指向別台 Python 路徑而無法執行：`deploy.ps1` 會偵測並自動刪除重建，且固定使用 `py -3.12` 建立
- build 完成後舊程式自動終止，新 exe 自動啟動
