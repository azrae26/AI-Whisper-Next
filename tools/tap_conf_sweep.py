#!/usr/bin/env python3
"""掃描不同門檻值，比較攔截率"""
import io, sys, pathlib, re

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tap_confidence_sim as sim

root = pathlib.Path(__file__).parent.parent / "tap_test_logs"
logs = sorted(root.glob("*.log"))

# 先收集所有候選的分數
all_scores = []   # (total, old_pass)
for p in logs:
    samples, dims, orig, _ = sim.parse_log(p)
    if not dims and not samples:
        continue
    results = sim.simulate(samples, dims)
    for t3, d, old_pass, _ in results:
        all_scores.append((d['total'], old_pass))

print(f"共 {len(all_scores)} 個候選\n")
print(f"{'門檻':>6} {'通過':>6} {'攔截':>6} {'攔截率':>8}")
print("-" * 32)
for thresh in [45, 50, 55, 60, 65, 70, 75]:
    passed  = sum(1 for s, o in all_scores if s >= thresh)
    blocked = sum(1 for s, o in all_scores if s < thresh)
    rate    = blocked / len(all_scores) * 100 if all_scores else 0
    print(f"{thresh:>6}  {passed:>6}  {blocked:>6}  {rate:>7.1f}%")

print()
print("分數分佈:")
buckets = {}
for s, _ in all_scores:
    b = (s // 10) * 10
    buckets[b] = buckets.get(b, 0) + 1
for b in sorted(buckets):
    bar = "█" * buckets[b]
    print(f"  {b:>3}-{b+9:<3}  {buckets[b]:>4}  {bar[:60]}")
