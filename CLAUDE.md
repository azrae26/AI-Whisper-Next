# AI Whisper Next — 專案說明（AGENTS.md）

> **同步規則**：本檔案（`AGENTS.md`）與 `CLAUDE.md` 內容應保持一致。
> 更新任一檔案的架構或規則時，**必須同步更新另一個檔案**。

---

## 專案概述

Windows 桌面語音轉文字工具。按熱鍵開始錄音，放開後自動辨識並貼至前景視窗。

## Tech Stack

- GUI：PySide6 (Qt 6)
- 語音辨識：OpenAI Audio API
- 錄音：sounddevice
- VAD：Silero VAD（優先）/ RMS 備援
- 熱鍵：Win32 RegisterHotKey + keyboard 套件
- 游標偵測：Windows UIAutomation (uiautomation)
- 文字後處理：opencc（簡轉繁）、cn2an（阿拉伯數字轉中文）、自訂替換
- 打包：PyInstaller

## 模組地圖

```
src/ai_whisper/
├── app.py                       # Entry: QApplication + 組裝 MainWindow / AppController
├── controller.py                # 核心控制器，串接所有 service 與 UI
├── models.py                    # AppConfig、TextCorrection、overlay_positions 資料結構
├── paths.py                     # 路徑管理
├── logging_setup.py             # 日誌設定
├── text_processing.py           # 文字後處理
├── services/
│   ├── audio_service.py         # 錄音、波形、分段 flush
│   ├── vad_service.py           # 語音活動偵測
│   ├── transcription_service.py # OpenAI API 呼叫 + retry
│   ├── paste_service.py         # 剪貼簿全格式備援、Ctrl+V／SendInput UNICODE、UIA、還原期護欄
│   ├── hotkey_service.py        # Win32 WM_HOTKEY 與 keyboard 並用、capture hook
│   ├── input_service.py         # 修飾鍵摘要／釋放、Ctrl+V 與 V、Unicode SendInput、Ctrl 護欄
│   └── settings_store.py       # JSON 設定讀寫
└── ui/
    ├── main_window.py           # 主視窗、系統匣、設定頁
    └── waveform_overlay.py      # 透明波形浮層、拖曳／重置面板、座標記憶
```

## 資料流

```
熱鍵
  → AppController.toggle_recording()
  → AudioService 開始錄音（sounddevice InputStream callback 累積 frames）
  → [定時檢查] 若觸發分段條件（靜音時間 / 累積長度）
      → flush frames → ThreadPoolExecutor（最多 4 個 worker）
      → VAD analyze_speech()  ← 過濾背景噪音、鍵盤聲
      → TranscriptionService → OpenAI API
      → PasteService →（符合啟發式時先試 SendInput UNICODE 驗證）備份剪貼簿 → Ctrl+V → 還原剪貼簿
  → 再按熱鍵停止 → 處理最後一段（同上）
  → final_done Signal → UI 更新
```

## 狀態機（AppController.state）

```
idle → recording → processing → idle
```

## Log 位置

- 一般從原始碼執行 / `restart` Skill 重啟：主程式 log **目錄**為 `paths.log_dir()`（專案根目錄下的 `logs/`）。**執行中**檔名為 `logs/ai_whisper_yyyyMMdd_HHmmss_{hostname}.current.log`，`{hostname}` 為本機電腦名經過 `logging_setup._sanitize_hostname()`（非英數改 `_`）；下次 `install_log_tee` **只會**將與本機相符的 `*_{hostname}.current.log` 改名為去掉 `.current` 的 `*_{hostname}.log`（細節以程式 `glob("*_{hostname}.current.log")` 為準），再開新一輪。**定稿後**即 `logs/ai_whisper_yyyyMMdd_HHmmss_{hostname}.log`。多機共用同步目錄時不會把另一台仍在寫的 `.current` 誤更名。`.gitignore` 忽略 `logs/*.current.log`。
- 打包後 exe 執行：同上規則，路徑在 exe 同層底下的 `logs/`（例如 `dist/AI Whisper/logs/`）。
- **`scripts/deploy.ps1`（PyInstaller Role build）** 會在刪除並替換整個 `dist/AI Whisper/` 前，將該目錄內 exe 既有的 `logs/` 複製到專案根的 `.pack_dist_exe_logs_stash/`（刻意不混入專案原始碼用的 `logs/`），新路徑就位後鏡射回 `dist/AI Whisper/logs/`；成功後暫存目錄清空，避免換 dist 洗掉舊紀錄。
- 分享用 zip 會排除 `dist/AI Whisper/logs/`，不包含任何本機執行 log；log 只保留在本機 dist 執行目錄。
- 敲麥 / tap 診斷分流：執行中為 `tap_test_logs/yyyyMMdd_HHmmss_{hostname}.current.log`，歸檔後為 `tap_test_logs/yyyyMMdd_HHmmss_{hostname}.log`；退役規則同主程式（僅 `*_{hostname}.current.log`）。`.gitignore` 忽略 `tap_test_logs/*.current.log`。只收主 log 裡含 `[tap]` 的行；打包後 exe 也會寫回專案根目錄的 `tap_test_logs/`。
- 打包流程 stdout/stderr：`dist/pack_yyyyMMdd_HHmmss.out.log` 與 `dist/pack_yyyyMMdd_HHmmss.err.log`。

---

## 非顯而易見的設計（重點）

### Warmup stream（預熱麥克風）
sounddevice InputStream 初始化要數百 ms。停止錄音後 stream **不立即關閉**，而是用 timer 延遲關閉（idle 超時後才真正 shutdown）。下次錄音命中預熱時直接開始，用戶感受不到延遲。
相關：`AudioService.start()` 中的 `self._stream is not None` 判斷、`AppController._schedule_warmup_shutdown()`。

### Prefetch cursor（預取游標位置）
UIA 查詢游標位置本身需要時間。送出 API 請求的**同時**，背景 thread 開始預取游標位置，並估算 API 回傳時間來決定何時開始查（避免太早查到舊位置、太晚查失去意義）。
API 回傳後直接使用已備好的結果，幾乎零等待。
相關：`PasteService.prefetch_cursor_position()`、`_consume_prefetch()`。

### Segment chaining（分段順序保證）
錄音中途可能觸發多次自動分段，每段各自在 thread 中送 API。為確保結果**按錄音順序**貼出，用 `threading.Event` 串鏈：每段等前一段 event 觸發後才執行貼上，貼完後 set 自己的 event。
相關：`AppController._prev_seg_event`、`_process_segment_audio()` 中的 `prev_event.wait()`。

### 剪貼簿完整備份還原
貼上前備份剪貼簿**所有格式**（不只 CF_UNICODETEXT），因為某些應用程式（如 Excel）會在剪貼簿內容改變時刷新自己的狀態，只還原文字格式會破壞原始資料。
相關：`PasteService._save_clipboard_all()`、`_restore_clipboard_all()`。

### 貼上前釋放修飾鍵（InputService）
用戶放開錄音熱鍵時，Shift／Alt／Windows… 仍可能為按下；貼上前由 `InputService.release_modifiers_for_paste()` 統一放開，避免標準 Ctrl+V 變成 Ctrl+Shift+V 等組合。**記憶貼文**等需保留 Ctrl 的情境可用 `preserve_ctrl_modifier`（對 `V` 送鍵）。
相關：`InputService.release_modifiers_for_paste`、`PasteService.paste_text()`。

### 句號前綴邏輯
貼上時若為空文字則直接返回 (early return)，不觸發後續剪貼簿與貼上流程。若為非空文字，且游標在文字末尾，且末尾不是標點，才加句號前綴（避免句句相連）。此判斷依賴 UIA TextPattern，若 UIA 不支援（如記事本某些版本）則不加。
相關：`PasteService._is_cursor_at_end()`、`PasteService._execute_paste()`。

### UIA 必須在固定 COM thread
`comtypes.CoInitialize()` 只對當前 thread 有效，UIA 查詢必須在同一個已初始化 COM 的 thread 執行。PasteService 為此有專屬的 `_paste_worker` thread（同時也序列化所有貼上操作）。Prefetch 的 UIA 查詢也各自在 thread 內呼叫 `CoInitialize`。

### VAD 雙引擎
Silero VAD 從 `torch.hub` 載入（首次需下載，之後快取），在背景 thread 預載以避免第一次錄音卡頓。若 torch 不可用則 fallback 到 RMS 能量閾值。Silero 準確率顯著優於 RMS（更能過濾遠端人聲、環境音）。
相關：`vad_service.preload_silero_vad()`、`analyze_speech()`。

### API Retry 機制
第一次請求超過閾值秒未回應，立刻**並行**發出第二次請求（不取消第一次），兩個 thread 競速，先回傳者獲勝。避免 OpenAI 偶發慢速時卡住用戶。
相關：`TranscriptionService.transcribe_with_retry()`。

### 全域熱鍵（Win32／keyboard／capture）
可解析為 Win32 mods+VK 之**錄音主熱鍵（句號／逗號）**與**五組記憶熱鍵**，在**同一**背景 thread 以 `RegisterHotKey` + `GetMessage`（`WM_HOTKEY`）監聽。其餘需由 `keyboard` 註冊的組合仍用 `keyboard.add_hotkey`。**錄製新快捷鍵**時改用 `keyboard` hook，`RegisterHotKey` 側須卸載並在結束錄製後重建。

### InputService（共用輸入層）
`AppController` 建立單例並注入 `PasteService`／`HotkeyService`。統一：`modifier_state_summary`、`release_modifiers_for_paste`（可選保留 Ctrl）、`send_ctrl_v`／`send_v`、`send_unicode_text`（SendInput UNICODE）、Ctrl 黏滯清理（延遲與立即）、熱鍵觸發後修飾鍵延遲釋放。Ctrl **狀態護欄**也由本模組負責啟停。

### Paste：Unicode 快速路徑與剪貼簿護欄
對符合前景啟發式的控制項（例如 Chrome Omnibox）可先試 **SendInput UNICODE** 直送並短延遲驗證；失敗或未命中則走「備份**所有格式** → 置入文字 → Ctrl+V → 延遲還原」主線。還原等待期若以 hotkey **攔下**使用者誤觸的 Ctrl+V（避免搶剪貼簿），於還原驗證成功後可由程式 **replay** 一次自動貼上。
相關：`PasteService` 前景視窗資訊、`_should_use_direct_text_input`。

### processing_started／錄製浮層收尾
`processing_started` 在確認 stop 後**確有可送辨識之音訊**時才發出（非一離開錄音即發）。自動分段皆完成並回 idle 時，波形浮層呼叫 `finish_recording_without_replay()`，收尾錄製狀態列而不強制回放波形。
相關：`AppController._process_final_audio()`／`_stop_recording`、`WaveformOverlay.finish_recording_without_replay`。

### Waveform 浮層位置（透明穿透與獨立按鈕視窗）
主波形視窗設 `WindowTransparentForInput`，滑鼠事件會穿透。**右緣 Hover** 會顯示獨立的 `_OverlayButtons` 小視窗（可接收真實點擊）：**重置**回到該螢幕預設底部置中並從設定移除該鍵；**拖曳區**經 Win32 `WM_NCHITTEST`→`HTCAPTION` 由系統原生拖曳。拖曳結束後會 **clamp** 在目前錨定的螢幕範圍內（不跨螢）、寫入 `AppConfig.overlay_positions`（鍵：`{hostname}/{QScreen.name}` → `x,y`；`hostname` 為 `socket.gethostname()` 即本機電腦名稱，**不同實體電腦鍵值不同**，同一 `config.json` 可並存多台各自的座標），經 `MainWindow.overlay_pos_changed` → `AppController._save_overlay_pos` 合併寫回 `config.json`；重置時對 callback 傳 `x=y=-1` 以移除該鍵。
相關：`WaveformOverlay._OverlayButtons`、`MainWindow.overlay_pos_changed`、`AppController._save_overlay_pos`、`SettingsStore`。

---

## 更新規則

架構或模組有任何變動時，同時更新 `CLAUDE.md` 與 `AGENTS.md`，保持兩份一致。

新增或修改 Skill 時，必須同步更新三個「專案內」位置的對應檔案，保持內容一致：
- Claude Code：`.claude/skills/<skill-name>/SKILL.md`
- Cursor：`.cursor/skills/<skill-name>/SKILL.md`
- Agents：`.agents/skills/<skill-name>/SKILL.md`

注意：AI Whisper Next 的 Skill 僅供本專案使用，不得放入任何工具的全域技能目錄。

### ⛔ 修改 .py 後必須重啟＋驗證（禁止跳過）

修改任何 `.py` 程式碼後，**必須執行 `restart` Skill 重啟應用程式**以使變更生效。
重啟成功後，**必須立即讀取並執行 `verify` Skill** 進行功能測試。

- **禁止**重啟成功就直接回覆用戶並結束。
- **禁止**跳過 verify 直接進行下一步操作。
- 正確流程：改碼 → `restart` → 確認啟動 → **`verify`** → 報告測試結果 → 才能結束或繼續。


