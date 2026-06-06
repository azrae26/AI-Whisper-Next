---
name: verify
description: 在 AI Whisper Next 重啟後，執行各功能模組（VAD、API 辨識、後處理、貼上）的測試與驗證。
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
  此腳本自動驗證：剪貼簿備份還原迴路、修飾鍵狀態、前景視窗偵測、完整貼上端到端流程（含 log 確認）。
  確保 10/10 全部通過。

* **修改了音訊處理管線 (audio_service.py) 或正規化邏輯**：
  執行完整管線測試，涵蓋 raw frames → process_frames（VAD + 正規化）→ API 辨識：
  ```powershell
  py -3.12 .agents/skills/verify/test_audio_pipeline.py
  ```
  此腳本驗證：process_frames 完整管線產出 WAV、正規化對小聲音訊有放大效果、正規化後音訊送 API 仍可正確辨識。
  確保 3/3 全部通過。

