---
name: push-and-package
description: 並行執行「推」與「包」，節省時間。TRIGGER: 使用者說「推 包」、「推包」、「推+包」時使用。
---

# 推 + 包 並行流程

git push 前景執行，PyInstaller 打包背景同步進行，大幅縮短等待時間。

## AI 執行流程（3 步完成）

### Step 1：取得 diff（1 次工具調用）

```powershell
git -C "f:\Cursor\AI Whisper Next" status
git -C "f:\Cursor\AI Whisper Next" diff
```

- 同時看 untracked 檔案，判斷哪些需要加入
- 根據 diff 撰寫 commit message（不可臆測）

### Step 2：並行啟動推 + 包（1 次工具調用）

背景啟動打包（Bash tool 加 `run_in_background: true`）：
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "f:\Cursor\AI Whisper Next\scripts\pack.ps1"
```

前景執行推：
```powershell
git -C "f:\Cursor\AI Whisper Next" add -u
# 若有 untracked 新檔案則額外 add
git -C "f:\Cursor\AI Whisper Next" commit -m "commit message 內容"
git -C "f:\Cursor\AI Whisper Next" pull origin main
git -C "f:\Cursor\AI Whisper Next" push origin main
```

推完成後立即回覆使用者。

### Step 3：確認背景狀態（選擇性）

打包在背景進行，zip 完成後 exe 自動啟動。

## 完成後告知

- **推**：git push 完成後立即回覆
- **包**：背景繼續，zip 產於 `dist/AI Whisper_yyyyMMdd_HHmm.zip`，exe 自動啟動

## zip 保留策略

`dist/` 底下的 `AI Whisper_*.zip` 只保留**最近 3 個**，其餘自動刪除。

## 注意事項

- 分支名稱為 `main`（非 `master`）
- 若想只推不包，直接用 git 指令；只包不推，用 build-and-package skill
