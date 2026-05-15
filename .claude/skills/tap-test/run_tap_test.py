"""
Tap detection simulation: reads current parameters from tap_service.py,
replays all trigger/rhythm-fail records from tap_test_logs/, and reports
pass/block counts.
"""
import re, os, sys

sys.stdout.reconfigure(encoding="utf-8")

# --- Read current params from source ---
src = open("src/ai_whisper/services/tap_service.py", encoding="utf-8").read()

def _param(name, cast):
    return cast(re.search(rf"{name}\s*=\s*([\d.]+)", src).group(1))

MIN_INT  = _param("TAP_MIN_INTERVAL_SEC", float)
MAX_INT  = _param("TAP_MAX_INTERVAL_SEC", float)
RHYTHM   = _param("TAP_RHYTHM_MIN_RATIO", float)
COUNT    = _param("TAP_COUNT",            int)
dur_tiers = re.findall(r"\((\d+(?:\.\d+)?|float\(\"inf\"\)),\s*([\d.]+)\)", src)
dur_str = "  ".join(
    f"{'∞' if 'inf' in lo else str(int(float(lo)//1000))+'k'}:{float(dur)*1000:.0f}ms"
    for lo, dur in dur_tiers
)

print(f"Parameters  MIN={MIN_INT*1000:.0f}ms  MAX={MAX_INT*1000:.0f}ms  "
      f"RHYTHM>={RHYTHM}  COUNT={COUNT}")
print(f"DUR thresholds  {dur_str}")
print()

# --- Parse test logs ---
TRIGGER_RE = re.compile(r"間隔 (\d+)ms / (\d+)ms.*一致性 ([\d.]+)")
LOG_DIR = "tap_test_logs"

triggers     = []  # (ia_ms, ib_ms, ratio, filename)
rhythm_fails = []

for fname in sorted(os.listdir(LOG_DIR)):
    if not fname.endswith(".log"):
        continue
    with open(os.path.join(LOG_DIR, fname), encoding="utf-8") as f:
        for line in f:
            m = TRIGGER_RE.search(line)
            if not m:
                continue
            ia, ib, ratio = int(m.group(1)), int(m.group(2)), float(m.group(3))
            if "三連敲觸發" in line:
                triggers.append((ia, ib, ratio, fname))
            elif "節奏不一致" in line:
                rhythm_fails.append((ia, ib, ratio, fname))

# --- Evaluate against current params ---
max_ms = MAX_INT * 1000
min_ms = MIN_INT * 1000

valid        = [(ia,ib,r,f) for ia,ib,r,f in triggers if r >= RHYTHM]
ok           = [(ia,ib,r,f) for ia,ib,r,f in valid    if max(ia,ib) <= max_ms and min(ia,ib) >= min_ms]
blocked_max  = [(ia,ib,r,f) for ia,ib,r,f in valid    if max(ia,ib) > max_ms]
blocked_min  = [(ia,ib,r,f) for ia,ib,r,f in valid    if min(ia,ib) < min_ms and max(ia,ib) <= max_ms]

# False-positive risk: rhythm_fail entries that would now pass all filters
false_pos = [
    (ia,ib,r,f) for ia,ib,r,f in rhythm_fails
    if max(ia,ib) <= max_ms and min(ia,ib) >= min_ms and r >= RHYTHM
]

print(f"觸發記錄總計: {len(triggers)}  (一致性 >= {RHYTHM} 的合法記錄: {len(valid)})")
print(f"  OK (通過全部條件): {len(ok)}")
print(f"  被 MAX({max_ms:.0f}ms) 封鎖:  {len(blocked_max)}")
print(f"  被 MIN({min_ms:.0f}ms) 封鎖:  {len(blocked_min)}")
print()
print(f"誤觸風險 (rhythm_fail 卻通過條件): {len(false_pos)}")

if blocked_max:
    print(f"\n--- 被 MAX 封鎖的合法觸發 ({len(blocked_max)} 筆) ---")
    for ia, ib, r, f in sorted(blocked_max, key=lambda x: max(x[0], x[1])):
        print(f"  {ia:4d}ms / {ib:4d}ms  r={r:.2f}  {f}")

if false_pos:
    print(f"\n--- 誤觸風險 ({len(false_pos)} 筆) ---")
    for ia, ib, r, f in false_pos:
        print(f"  {ia:4d}ms / {ib:4d}ms  r={r:.2f}  {f}")
