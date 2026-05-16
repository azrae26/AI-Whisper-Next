# AI Whisper Next — 專案說明（CLAUDE.md）

> **同步規則**：本檔案（`CLAUDE.md`）與 `AGENTS.md` 內容應保持一致。
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
├── models.py                    # AppConfig、TextCorrection 資料結構
├── paths.py                     # 路徑管理
├── logging_setup.py             # 日誌設定
├── text_processing.py           # 文字後處理
├── services/
│   ├── audio_service.py         # 錄音、波形、分段 flush
│   ├── vad_service.py           # 語音活動偵測
│   ├── transcription_service.py # OpenAI API 呼叫 + retry
│   ├── paste_service.py         # 剪貼簿操作、Ctrl+V、UIA 游標偵測
│   ├── hotkey_service.py        # 全域熱鍵註冊
│   └── settings_store.py       # JSON 設定讀寫
└── ui/
    ├── main_window.py           # 主視窗、系統匣、設定頁
    └── waveform_overlay.py      # 透明波形浮層
```

## 資料流

```
熱鍵
  → AppController.toggle_recording()
  → AudioService 開始錄音（sounddevice InputStream callback 累積 frames）
  → [定時檢查] 若觸發分段條件（靜音時間 / 累積長度）
      → flush frames → ThreadPoolExecutor（最多 6 個 worker）
      → VAD analyze_speech()  ← 過濾背景噪音、鍵盤聲
      → TranscriptionService → OpenAI API
      → PasteService → 備份剪貼簿 → Ctrl+V → 還原剪貼簿
  → 再按熱鍵停止 → 處理最後一段（同上）
  → final_done Signal → UI 更新
```

## 狀態機（AppController.state）

```
idle → recording → processing → idle
```

## Log 位置

- 一般從原始碼執行 / `restart` Skill 重啟：`ai_whisper_yyyyMMdd_HHmmss.log`，放在專案根目錄。
- 打包後 exe 執行：`dist/AI Whisper/ai_whisper_yyyyMMdd_HHmmss.log`，放在 exe 同層目錄。
- 敲麥 / tap 診斷分流：`tap_test_logs/yyyyMMdd_HHmmss.log`，只收主 log 裡含 `[tap]` 的行；打包後 exe 也會寫回專案根目錄的 `tap_test_logs/`。
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

### 貼上前釋放修飾鍵
用戶按熱鍵停止時，Shift / Alt 等鍵可能還處於按下狀態。`keyboard.send("ctrl+v")` 前先偵測並 release 這些鍵，否則會變成 `ctrl+shift+v` 等錯誤組合。
相關：`PasteService._release_paste_modifiers()`。

### 句號前綴邏輯
貼上時若游標在文字末尾，且末尾不是標點，才加句號前綴（避免句句相連）。此判斷依賴 UIA TextPattern，若 UIA 不支援（如記事本某些版本）則不加。
相關：`PasteService._is_cursor_at_end()`。

### UIA 必須在固定 COM thread
`comtypes.CoInitialize()` 只對當前 thread 有效，UIA 查詢必須在同一個已初始化 COM 的 thread 執行。PasteService 為此有專屬的 `_paste_worker` thread（同時也序列化所有貼上操作）。Prefetch 的 UIA 查詢也各自在 thread 內呼叫 `CoInitialize`。

### VAD 雙引擎
Silero VAD 從 `torch.hub` 載入（首次需下載，之後快取），在背景 thread 預載以避免第一次錄音卡頓。若 torch 不可用則 fallback 到 RMS 能量閾值。Silero 準確率顯著優於 RMS（更能過濾遠端人聲、環境音）。
相關：`vad_service.preload_silero_vad()`、`analyze_speech()`。

### API Retry 機制
第一次請求超過閾值秒未回應，立刻**並行**發出第二次請求（不取消第一次），兩個 thread 競速，先回傳者獲勝。避免 OpenAI 偶發慢速時卡住用戶。
相關：`TranscriptionService.transcribe_with_retry()`。

### 熱鍵兩種模式
主熱鍵用 Win32 `RegisterHotKey`（可在任意前景視窗觸發），但 capture 模式（用戶錄製新熱鍵）改用 `keyboard` 套件的 hook，因為 RegisterHotKey 無法捕捉所有按鍵組合。兩者不能同時運作，capture 期間先 unregister 主熱鍵。

---

## 更新規則

架構或模組有任何變動時，同時更新 `CLAUDE.md` 與 `AGENTS.md`，保持兩份一致。

新增或修改 Skill 時，必須同步更新三個「專案內」位置的對應檔案，保持內容一致：
- Claude Code：`.claude/skills/<skill-name>/SKILL.md`
- Cursor：`.cursor/skills/<skill-name>/SKILL.md`
- Agents：`.agents/skills/<skill-name>/SKILL.md`

注意：AI Whisper Next 的 Skill 僅供本專案使用，不得放入任何工具的全域技能目錄。

修改任何 `.py` 程式碼後，**必須執行 `restart` Skill 重啟應用程式**，確保變更生效。
