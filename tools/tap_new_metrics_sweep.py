#!/usr/bin/env python3
"""
掃描底噪 / ZCR 兩個新指標的最佳扣分參數。
用全誤觸 log 與全真敲 log 作為兩組 ground truth。
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import tap_confidence_sim as sim

root = Path(__file__).parent.parent / "tap_test_logs"
FP_LOG   = root / "20260517_205700.log"
REAL_LOG = root / "20260517_210300.log"

EXT_RE = re.compile(
    r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*三連敲觸發.*底噪=(-?\d+)/(-?\d+).*ZCR=([\d.]+)'
)

def parse_ext(path):
    ext = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = EXT_RE.search(line)
        if m:
            t = sim.parse_time(m.group(1))
            ext[round(t, 2)] = (max(int(m.group(2)), int(m.group(3))), float(m.group(4)))
    return ext

def lookup(ext, t3):
    if not ext: return None, None
    key = min(ext.keys(), key=lambda k: abs(k - t3))
    return ext[key] if abs(key - t3) < 0.5 else (None, None)

def score_new(floor_val, zcr_val, fp, fpts, zp, zpts):
    """fp/zp = 門檻；fpts/zpts = 每階扣分列表"""
    s = 0
    if floor_val is not None:
        for i, thresh in enumerate(fp):
            if floor_val < thresh:
                break
            s += fpts[i]
    if zcr_val is not None:
        for i, thresh in enumerate(zp):
            if zcr_val < thresh:
                break
            s += zpts[i]
    return s

def run(path, fp, fpts, zp, zpts, threshold):
    samples, dims, _, _ = sim.parse_log(path)
    results = sim.simulate(samples, dims)
    ext = parse_ext(path)
    total = blocked = 0
    details = []
    for t3, d, old_pass, _ in results:
        if not old_pass: continue
        total += 1
        fv, zv = lookup(ext, t3)
        penalty = score_new(fv, zv, fp, fpts, zp, zpts)
        final = d["total"] + penalty
        if final < threshold:
            blocked += 1
        details.append((d["total"], penalty, final, fv, zv))
    return total, blocked, details

# ─── 廣搜 ─────────────────────────────────────────────────────
# 底噪：兩段門檻 + 兩段扣分
FLOOR_THRESHOLDS = [
    (100, 200), (100, 250), (120, 220), (80, 180),
    (100, 300), (150, 300), (80, 150),
]
FLOOR_PTS = [(-10, -20), (-10, -25), (-10, -30), (-15, -25), (-15, -30), (-20, -30)]

# ZCR：兩段門檻 + 兩段扣分
ZCR_THRESHOLDS = [
    (0.112, 0.125), (0.112, 0.130), (0.115, 0.130),
    (0.110, 0.120), (0.115, 0.125), (0.118, 0.135),
]
ZCR_PTS = [(-8, -15), (-8, -20), (-10, -18), (-10, -22), (-12, -20)]

THRESHOLDS = [45, 50, 55]

results = []
for thresh in THRESHOLDS:
    for ft in FLOOR_THRESHOLDS:
        for fp_pts in FLOOR_PTS:
            for zt in ZCR_THRESHOLDS:
                for zp_pts in ZCR_PTS:
                    fp_t, fp_b, _ = run(FP_LOG,   list(ft), list(fp_pts), list(zt), list(zp_pts), thresh)
                    r_t,  r_b,  _ = run(REAL_LOG, list(ft), list(fp_pts), list(zt), list(zp_pts), thresh)
                    if fp_t == 0 or r_t == 0: continue
                    fp_rate  = fp_b / fp_t * 100
                    mis_rate = r_b  / r_t  * 100
                    score = fp_rate - mis_rate * 2.5
                    results.append((score, fp_rate, mis_rate, thresh, ft, fp_pts, zt, zp_pts, fp_b, fp_t, r_b, r_t))

results.sort(reverse=True)

print(f"共掃描 {len(results)} 組，Top 15：\n")
print(f"{'排名':>4}  {'門檻':>4}  攔FP        誤殺真敲   底噪門檻      底噪扣分    ZCR門檻          ZCR扣分")
print("-"*100)
for i, (sc, fpr, mr, thresh, ft, fp_pts, zt, zp_pts, fpb, fpt, rb, rt) in enumerate(results[:15]):
    print(f"  #{i+1:2d}  {thresh}  {fpb}/{fpt}({fpr:.0f}%)  {rb}/{rt}({mr:.0f}%)  "
          f"<{ft[0]}/<{ft[1]}  {fp_pts}  <{zt[0]}/<{zt[1]}  {zp_pts}")

# ─── 最佳組合詳細輸出 ─────────────────────────────────────────
print("\n=== 最佳組合逐筆明細 ===")
sc, fpr, mr, thresh, ft, fp_pts, zt, zp_pts, *_ = results[0]
print(f"門檻={thresh}  底噪:<{ft[0]}=0, <{ft[1]}={fp_pts[0]}, ≥{ft[1]}={fp_pts[1]}  "
      f"ZCR:<{zt[0]}=0, <{zt[1]}={zp_pts[0]}, ≥{zt[1]}={zp_pts[1]}")

for fname, label in [(FP_LOG, "FP"), (REAL_LOG, "REAL")]:
    _, _, details = run(fname, list(ft), list(fp_pts), list(zt), list(zp_pts), thresh)
    print(f"\n{label} ({fname.name}):")
    for base, pen, final, fv, zv in details:
        blocked = "❌攔截" if final < thresh else "✅通過"
        fv_s = str(int(fv)) if fv is not None else "?"
        zv_s = f"{zv:.3f}" if zv is not None else "?"
        print(f"  基礎={base:3d} 扣={pen:4d} 合計={final:3d}  底噪={fv_s:>5}  ZCR={zv_s}  {blocked}")
