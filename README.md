# AI Whisper Next

AI Whisper Next 是一個 Windows 桌面語音轉文字工具。它會從麥克風錄音，使用 OpenAI 音訊模型辨識繁體中文，並把結果貼到目前使用中的應用程式，也會保留最近幾筆辨識歷史方便重複使用。

這個專案是原本 AI Whisper 的 PySide6 重寫版，把錄音、VAD、轉寫、快捷鍵、UI Automation 貼上、剪貼簿備援和打包流程拆開，避免重工作卡住 UI。

## 功能

- 全域快捷鍵啟動錄音並貼上辨識的結果。
- 可分別設定句號、逗號兩種貼上快捷鍵。
- 支援選擇 OpenAI 語音辨識模型。
- 繁體中文正規化與自訂文字校正。
- 送出辨識前先做語音活動偵測，降低雜音誤辨。
- 長錄音時自動分段辨識。
- 保留最近辨識歷史，可快速複製。
- 錄音時系統列與工作列圖示會同步變色。
- 錄音波形浮層可拖曳調整位置，並依「本機電腦名稱／螢幕名稱」跨重啟記憶（可重置回預設）。
- 提供 Windows 打包腳本。

## 開發

詳細模組對照、資料流程與非顯然行為請見 **`AGENTS.md`** 與 **`CLAUDE.md`**（兩檔維護內容須保持一致）。

專案**固定使用 Python 3.12**（與 `pyproject.toml` 的 `requires-python`、打包腳本一致）。兩台以上電腦協作時，請各自安裝 3.12，並在**每一台**用下面方式建立 `.venv`；**不要**用同步軟體把別台複製過來的 `.venv` 帶過來（內含絕對路徑，易壞）。

```powershell
# 一次性：建立 .venv 並安裝依賴（需已安裝 Python 3.12 與 py launcher）
powershell -ExecutionPolicy Bypass -File .\scripts\setup-dev-venv.ps1

.\.venv\Scripts\Activate.ps1
python -m ai_whisper
```

若已裝過舊版或非 3.12 的 `.venv`，腳本會偵測並重建。使用 [pyenv-win](https://github.com/pyenv-win/pyenv-win) 時，專案根目錄的 `.python-version` 會指向 `3.12`。

## 打包

```powershell
.\scripts\pack.ps1
```

產生的打包檔案會被 Git 忽略，不會一起提交。

建置過程會以專案根目錄的 `.pack_dist_exe_logs_stash/` 暫存 **dist exe 底下的** `logs\`（刻意不混入專案根的 `logs/`，見 `scripts/deploy.ps1`），建好後再鏡射回 `dist\AI Whisper\logs\`，避免替換 dist 目錄時洗掉舊紀錄；成功結束後暫存目錄通常會清空。分享用 zip 會排除 `dist\AI Whisper\logs\`，不包含任何本機執行 log。

## 設定

程式會把本機設定存在 `config.json`（含選用的 `overlay_positions`：以**本機 Windows 電腦名稱**＋**螢幕名稱**為鍵，不同電腦互不覆蓋，同一份設定檔可並存多機的座標）。這個檔案可能包含 OpenAI API Key，所以已經刻意加入 `.gitignore`，不會提交到 GitHub。

如果目前資料夾沒有 `config.json`，程式可以讀取舊版 AI Whisper 專案裡相容的設定。

主程式除錯日誌在 `logs/`，格式為時間戳加上本機主機後綴，執行中為 `*.current.log`，下次啟動僅對**本機**上一輪執行轉正定檔（tap 同理；見 **`AGENTS.md`**／**`CLAUDE.md`**「Log 位置」）。

## 安全注意

- 不要提交真實 API Key。
- 不要提交打包輸出、虛擬環境或產生的 metadata。主程式 **`logs/`** 與敲麥 **`tap_test_logs/`** 可依需求納版本庫，若曾含機密或完整對話請先清理。
- 目前 `.gitignore` 已排除 `config.json`、多數 `*.log`（**`logs/`、`tap_test_logs/` 例外**）、執行中的 `logs/*.current.log` 與 `tap_test_logs/*.current.log`、`build/`、`dist/`、`.pack_dist_exe_logs_stash/`、虛擬環境和 package metadata。
