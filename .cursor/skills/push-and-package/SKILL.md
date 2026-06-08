---
name: push-and-package
description: 同時執行 git 推送與打包 AI Whisper Next。Use when the user says「推包」「推+包」「推 包」「推並打包」「推送並打包」「push and package」or any request that contains both push/git/commit intent and package/build/zip intent. This skill takes precedence over build-and-package when both apply. 單次需求只開一次 pack 管線，禁止為求保險再跑第二輪。
---

# 推 + 包 並行流程

git push 與 PyInstaller 打包**同時以背景任務啟動**，兩者輸出皆可捕獲，錯誤不會遺漏。

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

---

## 硬性規則（避免重複打包）⭐

1. 同一輪「推包」只允許**啟動一次** `pack.ps1`。
2. **一律用 `-WaitZip`**（與「包」skill 相同），build + zip 同步完成後才回傳，不會丟失輸出或錯誤。
3. **禁止**用 `Start-Process -WindowStyle Hidden` 背景啟動 `pack.ps1`——那樣抓不到輸出也看不到錯誤。
4. 僅在有**直接證據**指向第一輪失敗時（`pack.ps1` 明確報錯、非 0 exit）才重新發起單次 `pack`。

---

## AI 執行流程（2 步完成）

### Step 1：取得 diff

於專案根目錄執行：

```powershell
& "$env:USERPROFILE\.cursor\skills\git-push-workflow\status.ps1"
```

- 一併檢查 untracked，判斷是否要 `git add <檔案>`
- 根據 diff 撰寫 commit message（不可臆測）

### Step 2：推 + 包合一條命令（1 個背景任務）

push 只需 ~5 秒，不必拆開並行。**序列合成一條命令**，一個背景任務跑完：

```powershell
& "$env:USERPROFILE\.cursor\skills\git-push-workflow\push.ps1" "<commit message>"; powershell -ExecutionPolicy Bypass -File "scripts\pack.ps1" -WaitZip
```

任務完成時系統自動通知，確認輸出：

- **推**：有「OK push done」與 commit hash
- **包**：有 `zip: dist\AI Whisper_yyyyMMdd_HHmm.zip`
- 任一失敗則報錯，不靜默吞掉

---

## 回覆約束（使用者已知）

推／推包**成功**後，**不要**每次都用長段「心理準備」或教程式旁白，例如：

- 推送當下 `logs/`、`tap_test_logs/` 仍可能被**執行中的程式**改寫，導致 `git status` 出現 `M`／`??`；
- 背景打包時若同時開著舊版 **`AI Whisper.exe`**，理論上可能搶鎖或影響 zip。

上述情境**使用者已熟知**。除非使用者**明確追問**、或本次輸出顯示**明確失敗／錯誤**，否則只回報必要事實（commit hash、zip 路徑、分支等），**一句帶過或不提**即可。

## 完成後告知

- **推**：git push 成功後回報 commit hash
- **包**：回報 `dist/AI Whisper_yyyyMMdd_HHmm.zip` 路徑

## zip 保留策略

`dist/` 底下的 `AI Whisper_*.zip` 只保留**最近 3 個**，其餘自動刪除（與 **build-and-package** 相同）。

## 「包」的其餘行為（不重複鈔寫）

打包用 venv mapping、`config.json` 備份還原、`tar.exe`／`Compress-Archive`、build 完啟動新 exe 等，皆由 `scripts\deploy.ps1`／`pack.ps1` 實作；**以 build-and-package skill 的「注意事項」「完成後告知」為準**。

## 注意事項

- 分支以 `git branch --show-current` 為準（常為 `main`，非 `master`）
- **只推不包**：訊息僅「推」或「推送」時用 `git-push-workflow`；**只包不推**：用 `build-and-package` skill
