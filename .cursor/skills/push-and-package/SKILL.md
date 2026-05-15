---
name: push-and-package
description: 同時執行 git 推送與打包 AI Whisper Next。Use when the user says「推包」「推+包」「推 包」「推並打包」「推送並打包」「push and package」or any request that contains both push/git/commit intent and package/build/zip intent. This skill takes precedence over build-and-package when both apply. 單次需求只開一次 pack 管線，禁止為求保險再跑第二輪。
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

---

## 硬性規則（避免重複打包）⭐

下列情況在**同一則使用者需求**（同一次「推包」對話輪）內視為錯誤做法：

1. **已用 `Start-Process`／等同方式背景執行了 `scripts\pack.ps1`（不帶參數），之後又因「timestamp 仍是舊的」「想確認 zip」而再執行 `pack.ps1 -WaitZip` 或再跑一次無參數 `pack.ps1`**  
   → 會造成 **第二輪完整 PyInstaller build**，冗長且多餘，還可能與第一輪搶鎖／搶輸出目錄。

2. **正確態度**：這一輪只允許 **啟動一次** 打包進入點（`pack.ps1`）。

**若在推完後需要把「最新 zip 檔名」寫進回覆**：不要重跑打包；改為 **輪詢**（見下方 Step 3）。只有在使用者明確要求「這輪對話結束前要拿到 zip」且不接受背景時，才改用 **序列** `pack.ps1 -WaitZip`（見「模式 B」），且**不要使用**並行背景的 `pack.ps1` —— **二選一**。

---

## AI 執行流程（3 步完成）

### Step 1：取得 diff（1 次工具調用）

於專案根目錄執行：

```powershell
git status
git diff
```

- 一併檢查 untracked，判斷是否要 `git add <檔案>`
- 根據 diff 撰寫 commit message（不可臆測）

### Step 2：並行啟動推 + 包（預設：模式 A）

#### 模式 A（預設）：背景打包 + 前景推送

1. **背景**啟動打包：工作目錄設為專案根，執行（與 **build-and-package** skill 同一進入點；可選 `-NoProfile`）

   ```powershell
   powershell -ExecutionPolicy Bypass -File "scripts\pack.ps1"
   ```

   或以隱藏視窗委派（路徑用專案根變數，勿寫死槽位）：

   ```powershell
   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\pack.ps1' -WorkingDirectory '<repo-root>' -WindowStyle Hidden
   ```

   - 打包環境由 `deploy.ps1` 依 `$env:COMPUTERNAME` 判斷：家裡 `P8-32` 用 `.venv-pack`，公司機用 `.venv-pack_office`；若 venv 不存在或不可用，`deploy.ps1` 會用 `py -3.12` 重建。
   - 若用 `Start-Process` 背景啟動並要 redirect log，`-RedirectStandardOutput` 與 `-RedirectStandardError` 必須使用不同檔案；PowerShell 不允許兩者指向同一路徑。
   - **不帶參數**時：`pack.ps1` **前景**會先跑完 PyInstaller **build**，再把 **zip** 交給**另一個** hidden PowerShell **背景**執行。因此「剛開始推程式碼的那一瞬間」去看 `exe`/`zip`，仍可能是上一輪的時間戳——**這正常，不是失敗**，**禁止**為此立刻開第二輪 `pack`。

2. **前景**執行推送（與上式並行開始）：於專案根  

   - `git add -u`（必要時再 `git add` 指定未追蹤檔案）
   - `git commit -m "<message>"`
   - `git pull origin <當前分支>`
   - `git push origin <當前分支>`

**推**完成後即可先回覆使用者（commit hash、分支）；**包**在背景繼續到 zip 與 exe 啟動。

#### 模式 B（使用者明確要「本輪就要 zip 路徑／必須等壓縮完」）

- **不要**再並行 `Start-Process` 背景 `pack.ps1`。
- **改為**：先 push，再在前景執行（或先包再推，順序自定，但總時間較長）：

  ```powershell
  powershell -ExecutionPolicy Bypass -File "scripts\pack.ps1" -WaitZip
  ```

- 此一輪仍只跑一次 `pack.ps1`。與「推」同輪進行時，通常順序：**commit → pull → push → `pack.ps1 -WaitZip`**，避免 push 卡住時程式已包好卻送不上去。

### Step 3：確認背景打包（若要回報 zip 檔名，必用輪詢；禁止二次 pack）

若在模式 A 下希望回覆中包含 **確切的** `dist\AI Whisper_yyyyMMdd_HHmm.zip`：

1. **不得**為驗證而再執行 `pack.ps1` / `-WaitZip`。
2. 改為在等待 PyInstaller **之後**，對 `dist` **輪詢**最新產物，例如每隔 **30～45 秒** 檢查一次，最多約 **12～18 次**（總數分鐘級；build 約 90～180 秒視機器，zip 額外一小段）：
   - `Get-Item "dist\AI Whisper\AI Whisper.exe"` 的 `LastWriteTime`、與／或  
   - `Get-ChildItem "dist\AI Whisper_*.zip"` 依 `LastWriteTime` 取最新一個。  
   若最新 zip／exe 時間已晚於本輪開始推送的時間窗口，即可寫進回覆。

3. **若輪詢逾時**：回報推送已成功（commit）；說明 zip 尚在背景或未偵測到，請使用者在終端機執行 `scripts\pack.ps1 -WaitZip` 或數分鐘後自行查看 `dist\`。**仍不重跑 pack**。
4. 僅在有**直接證據**指向第一輪失敗時（例如 `deploy`/`pack` 明確報錯、build 資料夾出現異常、`pack.ps1` 非 0 exit）才調查並**重新**發起單次 `pack`，且應確認沒有其他 `pack`/PyInstaller 仍占住同一 `dist`。

## 完成後告知

- **推**：git push 成功後回報 commit hash
- **包（模式 A）**：可僅告知「打包與 zip 已在背景進行」，必要時附上輪詢得到的 `dist/AI Whisper_yyyyMMdd_HHmm.zip`；勿臆造檔名
- **包（模式 B）**：與 **build-and-package** 相同，直接回報 zip 路徑

## zip 保留策略

`dist/` 底下的 `AI Whisper_*.zip` 只保留**最近 3 個**，其餘自動刪除（與 **build-and-package** 相同）。

## 「包」的其餘行為（不重複鈔寫）

打包用 venv mapping、`config.json` 備份還原、`tar.exe`／`Compress-Archive`、build 完啟動新 exe 等，皆由 `scripts\deploy.ps1`／`pack.ps1` 實作；**以 build-and-package skill 的「注意事項」「完成後告知」為準**。

## 注意事項

- 分支以 `git branch --show-current` 為準（常為 `main`，非 `master`）
- **只推不包**：訊息僅「推」或「推送」時用 `git-push-workflow`；**只包不推**：用 `build-and-package` skill
