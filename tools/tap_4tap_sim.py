#!/usr/bin/env python3
"""模擬 4 連敲偵測，統計誤觸次數"""
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import tap_confidence_sim as sim

TAP_MIN = 0.18
TAP_MAX = 0.40
RHYTHM_MIN = 0.65

_DUR = [(8000,0.110),(10000,0.173),(12000,0.214),(float('inf'),0.214)]
def tap_max_dur(peak):
    for limit, dur in _DUR:
        if peak < limit: return dur

def simulate_4tap(samples, dims):
    tap_times, tap_peaks = [], []
    results = []
    for t, dur, peak, line in samples:
        if dur >= tap_max_dur(peak):
            tap_times.clear(); tap_peaks.clear()
            continue
        if tap_times:
            gap = t - tap_times[-1]
            if gap < TAP_MIN: continue
            if gap > TAP_MAX: tap_times.clear(); tap_peaks.clear()
        tap_times.append(t)
        tap_peaks.append(peak)
        if len(tap_times) >= 4:
            t1,t2,t3,t4 = tap_times[-4],tap_times[-3],tap_times[-2],tap_times[-1]
            p1,p2,p3,p4 = tap_peaks[-4],tap_peaks[-3],tap_peaks[-2],tap_peaks[-1]
            ia,ib,ic = t2-t1, t3-t2, t4-t3
            rhythm = min(ia,ib,ic)/max(ia,ib,ic) if max(ia,ib,ic)>0 else 1.0
            if rhythm >= RHYTHM_MIN:
                avg_peak = (p1+p2+p3+p4)/4
                results.append((t4, avg_peak, rhythm, ia,ib,ic))
                tap_times.clear(); tap_peaks.clear()
    return results

root = Path(__file__).parent.parent / 'tap_test_logs'

# 只跑最新那份「全誤觸」log
target = sorted(root.glob('*.log'))[-1]
print(f'分析: {target.name}')

samples, dims, orig, _ = sim.parse_log(target)
results = simulate_4tap(samples, dims)

print(f'  3敲偵測原始觸發 (節奏>=0.65): {len(orig)}')
print(f'  4敲偵測觸發: {len(results)}')
print()
if results:
    for t4, avg_peak, rhythm, ia,ib,ic in results:
        ts = f'{int(t4//3600):02d}:{int((t4%3600)//60):02d}:{t4%60:06.3f}'
        print(f'  {ts}  均峰={avg_peak:.0f}  節奏={rhythm:.2f}  間隔={ia*1000:.0f}/{ib*1000:.0f}/{ic*1000:.0f}ms')
else:
    print('  無任何 4 敲觸發')
