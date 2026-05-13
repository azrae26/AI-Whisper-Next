---
name: push-and-package
description: 同時執行 git 推送與打包 AI Whisper Next。Use when the user says「推包」「推+包」「推 包」「推並打包」「推送並打包」「push and package」or any request that contains both push/git/commit intent and package/build/zip intent. This skill takes precedence over build-and-package when both apply.
---

# 推 + 包 並行流程

git push **前景**執行，PyInstaller 打包 **背景**同步進行，縮短總等待時間。

## 觸發優先權

若使用者訊息同時包含：

- 推送意圖：`推`、`推送`、`push`、`commit`、`git`
- 打包意圖：`包`、`打包`、`build`、`zip`、`壓縮`

一律使用本 skill，不使用 `build-and-package`。

例：

- `推包` -> 本 skill
- `推+包` -> 本 skill
- `推送並打包` -> 本 skill
- `包` -> `build-and-package`
- `只包` -> `build-and-package`

**專案根目錄**：一律用當前 Cursor workspace 的 Git 根（本 repo：`AI Whisper Next`）。**禁止**在技能內寫死磁碟代號或絕對路徑（不同機器可能是 `D:\`、`F:\` 等）。

## AI 執行流程（3 步完成）

### Step 1：取得 diff（1 次工具調用）

於專案根目錄執行：

```powershell
git status
git diff
```

- 一併檢查 untracked，判斷是否要 `git add <檔案>`
- 根據 diff 撰寫 commit message（不可臆測）

### Step 2：並行啟動推 + 包（並行／接續視工具而定）

1. **背景**啟動打包：工作目錄設為專案根，執行（與 **build-and-package** skill 同一進入點；可選 `-NoProfile`）  
   `powershell -ExecutionPolicy Bypass -File "scripts\pack.ps1"`  
   - 打包環境固定使用專案根目錄的 `.venv-pack`，不得改用 `.venv-pack_office`；若 venv 不存在或不可用，`deploy.ps1` 會用 `py -3.12` 重建。  
   - 若用 `Start-Process` 背景啟動並要 redirect log，`-RedirectStandardOutput` 與 `-RedirectStandardError` 必須使用不同檔案；PowerShell 不允許兩者指向同一路徑。  
   - 預設**不帶參數**時行為與「包」相同：`pack.ps1` 先完成 PyInstaller **build**，再將 **zip** 以另一個 hidden PowerShell **背景**執行（不必等 zip 即可繼續推或回覆）。  
   - 若需與「包」skill 完全一致的其他模式，同一支腳本支援 `-BuildOnly`、`-WaitZip`（定義見 **build-and-package**）。  
   - Agent 單次執行若會等 build 跑完：建議逾時參考 build-and-package（build 約 300000ms、zip 約 60000ms）；推包並行時 zip 多在背景，以 build 時間為主。

2. **前景**執行推送（與上式並行開始）：於專案根  
   - `git add -u`（必要時再 `git add` 指定未追蹤檔案）  
   - `git commit -m "<message>"`  
   - `git pull origin <當前分支>`  
   - `git push origin <當前分支>`  

**推**完成後即可先回覆使用者；**包**可在背景繼續到 zip 與 exe 啟動。

### Step 3：確認背景狀態（選擇性）

打包完成後 zip 在 `dist\`，`pack.ps1` 會啟動新 exe。

## 完成後告知

- **推**：git push 成功後回報 commit hash
- **包**：背景完成時 zip 為 `dist/AI Whisper_yyyyMMdd_HHmm.zip`

## zip 保留策略

`dist/` 底下的 `AI Whisper_*.zip` 只保留**最近 3 個**，其餘自動刪除（與 **build-and-package** 相同）。

## 「包」的其餘行為（不重複鈔寫）

打包用 `.venv-pack`、`config.json` 備份還原、`tar.exe`／`Compress-Archive`、build 完啟動新 exe 等，皆由 `scripts\deploy.ps1`／`pack.ps1` 實作；**以 build-and-package skill 的「注意事項」「完成後告知」為準**。

## 注意事項

- 分支以 `git branch --show-current` 為準（常為 `main`，非 `master`）
- **只推不包**：訊息僅「推」或「推送」時用 `git-push-workflow`；**只包不推**：用 `build-and-package` skill
