# AI Whisper Next

AI Whisper Next 是一個 Windows 桌面語音轉文字工具。它會從麥克風錄音，使用 OpenAI 音訊模型辨識繁體中文，並把結果貼到目前使用中的應用程式，也會保留最近幾筆辨識歷史方便重複使用。

這個專案是原本 AI Whisper 的 PySide6 重寫版，把錄音、VAD、轉寫、快捷鍵、UI Automation 貼上、剪貼簿備援和打包流程拆開，避免重工作卡住 UI。

## 功能

- 全域快捷鍵啟動錄音並貼上辨識結果。
- 可分別設定句號、逗號兩種貼上快捷鍵。
- 支援選擇 OpenAI 語音辨識模型。
- 繁體中文正規化與自訂文字校正。
- 送出辨識前先做語音活動偵測，降低雜音誤辨。
- 長錄音時自動分段辨識。
- 保留最近辨識歷史，可快速複製。
- 錄音時系統列與工作列圖示會同步變色。
- 提供 Windows 打包腳本。

## 開發

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

## 設定

程式會把本機設定存在 `config.json`。這個檔案可能包含 OpenAI API Key，所以已經刻意加入 `.gitignore`，不會提交到 GitHub。

如果目前資料夾沒有 `config.json`，程式可以讀取舊版 AI Whisper 專案裡相容的設定。

## 安全注意

- 不要提交真實 API Key。
- 不要提交本機 log、打包輸出、虛擬環境或產生的 metadata。
- 目前 `.gitignore` 已排除 `config.json`、log、`build/`、`dist/`、虛擬環境和 package metadata。
