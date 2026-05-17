#!/usr/bin/env python3
"""
統計「說話期間觸發三連敲」（誤觸）的數量，以及信心評分能解決幾個。

判斷邏輯：
  真實觸發 = 三連敲候選裡，三敲均峰值 >= 1500（敲麥才有的力道）
  疑似誤觸 = 均峰值 < 1500（說話音量偶然湊成三連）

  （也列出 < 2000 的供參考）
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tap_confidence_sim as sim

PEAK_THRESHOLD  = 1200   # 均峰值 < 此值
RATIO_THRESHOLD = 0.40   # 雜訊比 > 此值
# 同時滿足兩條件 → 判為「說話誤觸」

root = pathlib.Path(__file__).parent.parent / "tap_test_logs"
logs = sorted(root.glob("*.log"))

total_fp = total_fp_blocked = total_real = 0
fp_details = []

for p in logs:
    samples, dims, orig, _ = sim.parse_log(p)
    if not dims and not samples:
        continue
    results = sim.simulate(samples, dims)
    for t3, d, old_pass, new_pass in results:
        is_fp = d['avg_peak'] < PEAK_THRESHOLD and d['ratio'] > RATIO_THRESHOLD
        if is_fp:
            total_fp += 1
            fp_details.append((d['total'], d['avg_peak'], d['ratio'], new_pass))
            if not new_pass:
                total_fp_blocked += 1
        else:
            total_real += 1

print(f"誤觸判定條件：均峰 < {PEAK_THRESHOLD}  且  雜訊比 > {RATIO_THRESHOLD}")
print(f"信心評分門檻：>= {sim.CONF_TRIGGER}")
print()
print(f"總候選數                 : {total_fp + total_real}")
print(f"真實敲擊                 : {total_real}")
print(f"疑似說話誤觸             : {total_fp}")
if total_fp:
    pct = total_fp_blocked / total_fp * 100
    print(f"  → 信心評分攔住       : {total_fp_blocked}  ({pct:.0f}%)")
    print(f"  → 漏網（仍放行）     : {total_fp - total_fp_blocked}")
print()
print("各誤觸的信心分數：")
for score, peak, ratio, blocked in sorted(fp_details):
    tag = "攔" if not blocked else "漏"
    print(f"  [{tag}] 分={score:3d}  均峰={peak:5.0f}  雜訊比={ratio:.2f}")
