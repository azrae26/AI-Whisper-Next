---
name: verify
description: 在 AI Whisper Next 重啟後，執行各功能模組（VAD、API 辨識、後處理、貼上、並發）的測試與驗證。也負責邊緣場景的回歸測試：發現潛在 bug 時先寫測試確認問題存在，修完後再測確認解決。
---

# AI Whisper Next 測試與驗證指引

每次重啟或修改程式碼後，**必須**依照此 Skill 進行實際驗證。禁止僅憑重啟成功即結束任務。

## 1. 基本存活檢查
執行 ping 確保 App 存活且 Debug Server 已啟動：
```powershell
py scripts/debug_query.py ping
```

## 2. 針對改動模組進行功能驗證
根據修改的檔案，執行對應的測試，確認功能無誤且無 Bug：

* **若有修改 VAD (vad_service.py) 或錄音相關邏輯**：
  **絕不可只驗證 API 識別**，必須同時驗證 VAD 判定。執行專案內的 VAD 測試：
  ```powershell
  $env:PYTHONPATH=".\.venv-pack\Lib\site-packages"; py -3.12 .agents/skills/verify/test_vad.py
  ```
  確保 `has_speech` 為 `True` 且 early exit 與比例門檻運作正常。

* **僅驗證 API 傳輸與識別功能**：
  使用 `tests/golden_speech.wav` 透過 eval 送辨識測試：
  ```powershell
  py scripts/debug_query.py eval "self.transcription.transcribe_clean(open('tests/golden_speech.wav','rb').read(), self.cfg.apiKey, self.cfg.model, self.cfg.corrections)"
  ```

* **修改了文字後處理 (text_processing.py)**：
  連同辨識結果一併測試，或用 eval 測試轉換邏輯是否符合預期。

* **修改了貼上邏輯 (paste_service.py / input_service.py)**：
  執行專案內的貼上測試腳本（會實際觸發貼上到前景視窗）：
  ```powershell
  py -3.12 .agents/skills/verify/test_paste.py
  ```
  此腳本覆蓋三種貼上方法 × 常用程式的端到端驗證：

  | 方法 | 原理 | 優勢 | 限制 |
  |------|------|------|------|
  | **SendInput UNICODE** | 硬體鍵盤事件 | 最快、不動剪貼簿 | Qt/Electron 非空輸入框中間插入會吞字 |
  | **WM_CHAR PostMessage** | 視窗訊息直送 HWND | 相容 Qt/Electron | 需開啟設定 toggle |
  | **Ctrl+V 剪貼簿** | 備份→設文字→Ctrl+V→還原 | 最穩、全應用相容 | 剪貼簿副作用、速度較慢 |

  已知限制（測試中自動跳過）：
  - **LINE (Qt 6) + SendInput**：非空輸入框中間插入會吞字/亂序，改用 WM_CHAR 解決

  每個常用程式（LINE/Chrome/Antigravity/Cursor/Codex）測完三種方法再換下一個，
  另有 WM_CHAR 中間插入測試（非空輸入框第 4 字後插入，UIA 讀回驗證）。

* **修改了音訊處理管線 (audio_service.py) 或正規化邏輯**：
  執行完整管線測試，涵蓋 raw frames → process_frames（VAD + 正規化）→ API 辨識：
  ```powershell
  py -3.12 .agents/skills/verify/test_audio_pipeline.py
  ```
  此腳本驗證：process_frames 完整管線產出 WAV、正規化對小聲音訊有放大效果、正規化後音訊送 API 仍可正確辨識。
  確保 3/3 全部通過。

* **修改了並發邏輯 (controller.py / transcription_service.py / paste_service.py)**：
  執行並發場景測試，涵蓋線程池飢餓、API 雙軌 Retry、UIA Worker Queue 隔離：
  ```powershell
  py -3.12 .agents/skills/verify/test_concurrency.py
  ```
  此腳本驗證：線程池滿時 Thread 仍可執行（修復1）、第一軌 error 後第二軌仍能救場（修復2）、舊 worker 恢復後不搶新 Queue（修復3）。
  確保 10/10 全部通過。

## 3. 邊緣場景回歸測試

每次發現一個可能或實際的邊緣場景（edge case）時，**必須**遵循以下流程，確保測試套件隨時間越來越強壯：

### 流程（測試先行）

1. **先寫測試**：在對應的測試腳本中加入模擬該邊緣場景的測試案例。
2. **確認 bug 存在**：執行測試，確認在修改前該測試確實會失敗或重現問題（用對照組類別模擬舊行為）。
3. **開始修改程式碼**。
4. **再測一遍確認解決**：修改完成後重新執行同一測試，確認通過。

### 歸檔規則

依類別將測試加到對應的既有測試腳本中：

| 類別 | 測試檔 |
|------|--------|
| VAD / 語音判定 | `test_vad.py` |
| 貼上 / 剪貼簿 / 修飾鍵 | `test_paste.py` |
| 音訊管線 / 正規化 | `test_audio_pipeline.py` |
| SendInput 相容性 | `test_sendinput_compat.py` |
| 並發 / 線程安全 / 狀態機 | `test_concurrency.py` |

若邊緣場景不屬於上述任一類別，才建立新的 `test_<category>.py`，並同步更新本 SKILL.md 的第 2 節。

### 測試品質要求

- **測試真實行為，禁止弱測試**：每個測試必須驗證**實際行為結果**（return value、log 輸出、狀態變化），不能只檢查結構（方法存在、屬性型別、常數值）。結構檢查不算測試，因為它無法證明程式在執行時走了正確的分支。

  | 弱測試（❌ 禁止） | 強測試（✅ 要求） |
  |-------------------|-------------------|
  | `hasattr(obj, 'method')` | mock 輸入 → 呼叫 method → 檢查回傳值或 log |
  | `isinstance(x, Lock)` | 兩個 thread 併發寫入 → 檢查最終資料完整 |
  | `callable(fn)` | 觸發 fn → 驗證副作用（log、檔案、狀態） |

- **測試要有對照組**：不只測「修完後正確」，也要模擬舊行為確認 bug 確實存在，這樣測試才有證明力。
- **優先確定性測試**：能用語言層級保證（如 Python attribute lookup、ThreadPoolExecutor 語意）驗證的，優於靠概率觸發的競爭測試。概率測試需高迭代次數（≥20）+ Barrier 同步起跑。
