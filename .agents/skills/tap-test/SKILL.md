---
name: tap-test
description: 每次修改 tap_service.py 的音頻判斷條件後，對 tap_test_logs/ 跑模擬測試，驗證新參數不會造成合法觸發被封鎖或新增誤觸風險。
---

# Tap 參數模擬測試

## 觸發時機

每次修改 `tap_service.py` 中的任何偵測參數後自動執行：

- `TAP_MIN_INTERVAL_SEC`
- `TAP_MAX_INTERVAL_SEC`
- `TAP_MAX_DURATION_SEC`
- `TAP_RHYTHM_MIN_RATIO`
- `TAP_COUNT`

## 執行指令

```powershell
py -3.12 .claude/skills/tap-test/run_tap_test.py
```

在 workspace 根目錄執行，timeout 10000ms。

## 輸出解讀

腳本會從 `tap_service.py` 讀取現行參數，對 `tap_test_logs/` 內所有 log 進行模擬，輸出：

- **OK**：通過全部條件的合法觸發數
- **被 MAX 封鎖**：因間隔過長被拒的合法觸發（應盡量低）
- **被 MIN 封鎖**：因間隔過短被拒的合法觸發（通常為 0）
- **誤觸風險**：原本節奏不一致被過濾，但在新參數下卻通過的記錄（必須為 0）

## 回覆格式

執行完畢後，直接輸出腳本結果，並加一行結論：

- 若「誤觸風險 = 0」且「被封鎖比例 < 15%」→ `✅ 參數安全`
- 若「誤觸風險 > 0」→ `⚠️ 有誤觸風險，需調整`
- 若「被封鎖比例 >= 15%」→ `⚠️ 封鎖過多合法觸發，考慮放寬 MAX`

## 注意事項

- 使用 `py -3.12`，不使用裸 `py`
- test logs 為歷史記錄，一致性 < RHYTHM 的舊觸發不計入合法觸發
- 測試完不需要重啟應用程式
