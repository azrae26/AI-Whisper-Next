#!/usr/bin/env python3
"""
找出真正的誤觸：三連敲觸發 → 開始錄音 → 但辨識結果為空或 VAD 拒絕
這才是使用者沒打算錄音的情況。
"""
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tap_confidence_sim as sim

tap_log_dir  = pathlib.Path(__file__).parent.parent / "tap_test_logs"
main_log_dir = pathlib.Path(__file__).parent.parent / "logs"

TRIGGER_RE  = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*三連敲觸發')
START_RE    = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*🎙️ 開始錄音')
DONE_RE     = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*✅ (?:辨識完成|錄音完成)')
VAD_FAIL_RE = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*VAD 未達有效語音')
RECOG_RE    = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*(?:✅ 辨識完成|✅ 分段辨識完成): "(.+)"')

PAIR_WINDOW = 30.0  # 觸發後最多幾秒內的錄音結果算配對

def parse_time(ts):
    h, m, rest = ts.split(':')
    s, ms = rest.split('.')
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

total_trig = total_good = total_fp = total_fp_blocked = 0
fp_rows = []

for main_log in sorted(main_log_dir.glob("ai_whisper_202605*.log")):
    stem = main_log.stem.replace("ai_whisper_", "")
    tap_log = tap_log_dir / f"{stem}.log"
    if not tap_log.exists():
        continue

    lines = main_log.read_text(encoding='utf-8', errors='replace').splitlines()

    # 收集 events
    triggers, starts, recog_results, vad_fails = [], [], [], []
    for line in lines:
        if m := TRIGGER_RE.search(line):
            triggers.append(parse_time(m.group(1)))
        if m := START_RE.search(line):
            starts.append(parse_time(m.group(1)))
        if m := RECOG_RE.search(line):
            recog_results.append((parse_time(m.group(1)), m.group(2)))
        if m := VAD_FAIL_RE.search(line):
            vad_fails.append(parse_time(m.group(1)))

    if not triggers:
        continue

    # 信心評分 map
    samples, dims, _, _ = sim.parse_log(tap_log)
    results_map = {}
    if dims or samples:
        for t3, d, old_pass, new_pass in sim.simulate(samples, dims):
            results_map[round(t3, 2)] = (d, old_pass, new_pass)

    for t_trig in triggers:
        total_trig += 1

        # 找此觸發後最近的開始錄音
        next_starts = [s for s in starts if 0 <= s - t_trig <= PAIR_WINDOW]
        if not next_starts:
            total_good += 1
            continue
        t_start = min(next_starts)

        # 找此錄音後的辨識結果（t_start 後 30 秒內）
        next_recogs = [(t, txt) for t, txt in recog_results if 0 < t - t_start <= PAIR_WINDOW]
        next_vads   = [t for t in vad_fails if 0 < t - t_start <= PAIR_WINDOW]

        has_text = bool(next_recogs)
        vad_only = bool(next_vads) and not has_text

        if has_text:
            total_good += 1
        else:
            # 觸發但沒產生辨識文字 → 誤觸
            total_fp += 1
            matched = None
            for k, v in results_map.items():
                if abs(k - t_trig) < 0.5:
                    matched = v
                    break
            blocked = False
            score = -1
            if matched:
                d_info, old_pass, new_pass = matched
                blocked = not new_pass
                score = d_info['total']
            if blocked:
                total_fp_blocked += 1
            reason = "VAD拒絕" if vad_only else "無辨識"
            fp_rows.append((stem, t_trig, score, blocked, reason))

if total_fp:
    pct = total_fp_blocked / total_fp * 100
else:
    pct = 0

print(f"總三連敲觸發           : {total_trig}")
print(f"有產生辨識文字（真敲）  : {total_good}")
print(f"無辨識文字（誤觸）      : {total_fp}")
print(f"  → 信心評分攔住       : {total_fp_blocked}  ({pct:.0f}%)")
print(f"  → 漏網               : {total_fp - total_fp_blocked}")
print()
if fp_rows:
    # 統計分數分佈
    scored = [s for _, _, s, _, _ in fp_rows if s >= 0]
    if scored:
        print(f"誤觸信心分數分佈（門檻={sim.CONF_TRIGGER}）：")
        buckets = {}
        for s in scored:
            b = (s // 10) * 10
            buckets[b] = buckets.get(b, 0) + 1
        for b in sorted(buckets):
            tag = "攔" if b < sim.CONF_TRIGGER else "漏"
            print(f"  [{tag}] {b}-{b+9}分: {buckets[b]:>3} 個")
    print()
    print(f"{'log':24} {'時間':10} {'分數':>5} {'原因':>6} {'結果'}")
    print("-" * 60)
    for stem, t, score, blocked, reason in fp_rows:
        ts = f"{int(t//3600):02d}:{int((t%3600)//60):02d}:{t%60:06.3f}"
        s_str = f"{score}" if score >= 0 else "?"
        r_str = "攔截 ✓" if blocked else "漏網 ✗"
        print(f"  {stem:22}  {ts}  {s_str:>5}  {reason:>6}  {r_str}")
