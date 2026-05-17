#!/usr/bin/env python3
"""
tap_confidence_sim.py
對新格式 tap_test_logs/ (含 [tap][dim] 與 [tap][sample] 時間戳) 模擬信心評分，
輸出每次三連敲觸發的評分明細，並與原始結果比對準確度。

用法：
    python tools/tap_confidence_sim.py [log_file_or_dir ...]
    (不帶參數則掃描 tap_test_logs/ 所有含 [tap][dim] 的新格式 log)
"""

import io
import re
import sys
from pathlib import Path

# 強制 stdout UTF-8（Windows cp950 會炸）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════════
#  評分參數（此處調整，其餘程式碼不用動）
# ══════════════════════════════════════════════════════════════

CONF_TRIGGER = 58          # 觸發所需最低信心分數（0-100）

# ① 峰值強度 (0-30) ── 3 敲平均峰值
CONF_PEAK_TABLE = [        # [(最低峰值, 得分), ...] 由高到低
    (3000, 30),
    (2000, 25),
    (1000, 20),
    ( 700, 10),
    (   0,  0),
]

# ② 前靜音 (0-25) ── #1 之前 CONF_SILENCE_WIN 秒內的 dim 事件數
CONF_SILENCE_WIN   = 0.50  # 觀察窗口（秒）
CONF_SILENCE_TABLE = [     # [(最多幾個 dim, 得分), ...]
    (0, 25),
    (1,  0),   # 1 個 dim 不加分（加嚴）
    (2,  0),
]

# ③ 雜訊比 (0-30) ── 窗口內最高 dim 峰值 ÷ 3 敲均峰值
CONF_DIM_WIN   = 0.15      # #1 前 / #3 後各延伸多少秒
CONF_DIM_TABLE = [         # [(比值上限, 得分), ...] 比值越低越好
    (0.30, 30),
    (0.50, 20),
    (0.70, 10),
    (1.00,  0),
]

# ④ 節奏一致性 (0-15)
CONF_RHYTHM_TABLE = [      # [(最低一致性, 得分), ...]
    (0.90, 15),
    (0.80, 10),
    (0.65,  5),
    (0.00,  0),
]

# ⑤ 底噪扣分 (0 to -20) ── 敲擊間靜音的最大 RMS（越高表示背景有持續聲音）
CONF_FLOOR_TABLE = [       # [(底噪上限, 扣分), ...] 超過門檻才扣
    ( 150,   0),
    ( 300, -10),
    (float("inf"), -20),
]

# ⑥ ZCR 扣分 (0 to -20) ── 零交叉率（語音 > 敲擊聲）
CONF_ZCR_TABLE = [         # [(ZCR上限, 扣分), ...] 超過門檻才扣
    (0.118,   0),
    (0.135,  -8),
    (float("inf"), -20),
]

# ── 原有過濾參數（維持不動，作為前置過濾）──────────────────
TAP_MIN_INTERVAL = 0.18    # 秒：< 此值 → 防抖忽略
TAP_MAX_INTERVAL = 0.40    # 秒：> 此值 → 重置序列
TAP_DUR_THRESHOLDS = [     # (峰值上限, 最大持續時間)
    (8000,  0.110),
    (10000, 0.173),
    (12000, 0.214),
    (float("inf"), 0.214),
]

# ══════════════════════════════════════════════════════════════

def tap_max_dur(peak: float) -> float:
    for limit, dur in TAP_DUR_THRESHOLDS:
        if peak < limit:
            return dur
    return TAP_DUR_THRESHOLDS[-1][1]


def score_ge(value: float, table: list) -> int:
    """table: [(min_value, pts), ...] 由高到低 → 回傳第一個 value >= min_value 的分數"""
    for threshold, pts in table:
        if value >= threshold:
            return pts
    return 0


def score_lt(value: float, table: list) -> int:
    """table: [(upper_limit, pts), ...] → 回傳第一個 value < upper_limit 的分數"""
    for limit, pts in table:
        if value < limit:
            return pts
    return 0


def parse_time(ts: str) -> float:
    """HH:MM:SS.mmm → 秒（自午夜起算）"""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


# ── 解析 ─────────────────────────────────────────────────────

LINE_RE = re.compile(
    r'\[tap\]\[(sample|dim)\]\s*\[(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*'
    r'持續=(\d+)ms\s+峰值=(\d+)'
)
TRIGGER_RE = re.compile(r'三連敲觸發（間隔 ([\d.]+)ms / ([\d.]+)ms，一致性 ([\d.]+)）')
EXT_RE = re.compile(
    r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\].*三連敲觸發.*底噪=(-?\d+)/(-?\d+).*ZCR=([\d.]+)'
)


def parse_log(path: Path):
    """回傳 (sample_events, dim_events, original_triggers, ext_metrics)
       sample_events:   list of (time, dur_sec, peak, raw_line)
       dim_events:      list of (time, peak)
       original_triggers: list of (ia_ms, ib_ms, ratio)
       ext_metrics:     dict of {round(time,2): (floor, zcr)}  — 新格式 log 才有
    """
    samples, dims, triggers = [], [], []
    ext_metrics = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE_RE.search(line)
        if m:
            kind, ts_str, dur_ms, peak_str = m.groups()
            t = parse_time(ts_str)
            dur = int(dur_ms) / 1000
            peak = float(peak_str)
            if kind == "sample":
                samples.append((t, dur, peak, line))
            else:
                dims.append((t, peak))
            continue
        m2 = TRIGGER_RE.search(line)
        if m2:
            ia, ib, ratio = float(m2.group(1)), float(m2.group(2)), float(m2.group(3))
            triggers.append((ia, ib, ratio))
        m3 = EXT_RE.search(line)
        if m3:
            t = parse_time(m3.group(1))
            floor_val = max(int(m3.group(2)), int(m3.group(3)))
            zcr_val = float(m3.group(4))
            ext_metrics[round(t, 2)] = (floor_val, zcr_val)
    return samples, dims, triggers, ext_metrics


# ── 信心評分 ─────────────────────────────────────────────────

def compute_confidence(t1, t2, t3, p1, p2, p3,
                        dims: list,
                        floor: float = -1,
                        zcr: float = -1) -> tuple[int, dict]:
    avg_peak = (p1 + p2 + p3) / 3

    # ① 峰值強度
    peak_pts = score_ge(avg_peak, CONF_PEAK_TABLE)

    # ② 前靜音
    dims_before = [p for t, p in dims if t1 - CONF_SILENCE_WIN <= t < t1]
    n_before = len(dims_before)
    silence_pts = 0
    for max_n, pts in CONF_SILENCE_TABLE:
        if n_before <= max_n:
            silence_pts = pts
            break

    # ③ 雜訊比
    win_start, win_end = t1 - CONF_DIM_WIN, t3 + CONF_DIM_WIN
    dims_win = [p for t, p in dims if win_start <= t <= win_end]
    if dims_win:
        max_dim = max(dims_win)
        ratio = max_dim / avg_peak if avg_peak > 0 else 1.0
    else:
        max_dim, ratio = 0.0, 0.0
    dim_pts = score_lt(ratio, CONF_DIM_TABLE)

    # ④ 節奏
    ia, ib = t2 - t1, t3 - t2
    rhythm = min(ia, ib) / max(ia, ib) if max(ia, ib) > 0 else 1.0
    rhythm_pts = score_ge(rhythm, CONF_RHYTHM_TABLE)

    # ⑤ 底噪扣分（需新格式 log）
    floor_pts = 0
    if floor >= 0:
        floor_pts = score_lt(floor, CONF_FLOOR_TABLE)

    # ⑥ ZCR 扣分（需新格式 log）
    zcr_pts = 0
    if zcr >= 0:
        zcr_pts = score_lt(zcr, CONF_ZCR_TABLE)

    total = peak_pts + silence_pts + dim_pts + rhythm_pts + floor_pts + zcr_pts
    detail = dict(
        total=total,
        peak=peak_pts, avg_peak=round(avg_peak),
        silence=silence_pts, n_before=n_before,
        dim_ratio=dim_pts, ratio=round(ratio, 2), max_dim=round(max_dim),
        rhythm=rhythm_pts, rhythm_val=round(rhythm, 2),
        ia_ms=round(ia * 1000), ib_ms=round(ib * 1000),
        floor_pts=floor_pts, floor=round(floor) if floor >= 0 else -1,
        zcr_pts=zcr_pts, zcr=round(zcr, 3) if zcr >= 0 else -1,
    )
    return total, detail


# ── 模擬偵測 ─────────────────────────────────────────────────

def simulate(samples, dims, ext_metrics: dict | None = None):
    """重跑偵測邏輯，回傳每個三連敲候選的 (t3, detail, old_pass, new_pass)
       ext_metrics: {round(time,2): (floor, zcr)} — 來自 parse_log 的新格式資料
    """
    tap_times, tap_peaks = [], []
    results = []

    for t, dur, peak, line in samples:
        # 長音過濾
        if dur >= tap_max_dur(peak):
            tap_times.clear(); tap_peaks.clear()
            continue
        # 防抖 / 間隔重置
        if tap_times:
            gap = t - tap_times[-1]
            if gap < TAP_MIN_INTERVAL:
                continue
            if gap > TAP_MAX_INTERVAL:
                tap_times.clear(); tap_peaks.clear()

        tap_times.append(t)
        tap_peaks.append(peak)

        if len(tap_times) >= 3:
            t1, t2, t3 = tap_times[-3], tap_times[-2], tap_times[-1]
            p1, p2, p3 = tap_peaks[-3], tap_peaks[-2], tap_peaks[-1]
            ia, ib = t2 - t1, t3 - t2
            rhythm = min(ia, ib) / max(ia, ib) if max(ia, ib) > 0 else 1.0

            # 查找最近的 ext_metrics（底噪 / ZCR）
            floor_val = zcr_val = -1.0
            if ext_metrics:
                best = min(ext_metrics.keys(), key=lambda k: abs(k - t3), default=None)
                if best is not None and abs(best - t3) < 0.5:
                    floor_val, zcr_val = ext_metrics[best]

            old_pass = rhythm >= 0.65
            total, detail = compute_confidence(t1, t2, t3, p1, p2, p3, dims,
                                               floor=floor_val, zcr=zcr_val)
            new_pass = total >= CONF_TRIGGER

            results.append((t3, detail, old_pass, new_pass))
            tap_times.clear(); tap_peaks.clear()

    return results


# ── 輸出 ─────────────────────────────────────────────────────

def fmt_score(d: dict) -> str:
    base = (f"總={d['total']:3d}  "
            f"峰值={d['peak']:2d}(均{d['avg_peak']:.0f})  "
            f"前靜={d['silence']:2d}({d['n_before']}dim)  "
            f"雜訊={d['dim_ratio']:2d}(比{d['ratio']:.2f})  "
            f"節奏={d['rhythm']:2d}({d['rhythm_val']:.2f},"
            f"{d['ia_ms']}ms/{d['ib_ms']}ms)")
    if d.get('floor', -1) >= 0:
        base += (f"  底噪={d['floor_pts']:3d}(={d['floor']:.0f})"
                 f"  ZCR={d['zcr_pts']:3d}(={d['zcr']:.3f})")
    return base


def run_file(path: Path, verbose: bool = True) -> dict:
    samples, dims, orig_triggers, ext_metrics = parse_log(path)
    if not dims and not samples:
        return {}          # 不是新格式，跳過

    results = simulate(samples, dims, ext_metrics)
    if not results:
        return {}

    tp = fp = fn = blocked = 0
    lines = []

    for t3, d, old_pass, new_pass in results:
        status = ""
        if old_pass and new_pass:
            status = "✅ 雙通過"; tp += 1
        elif old_pass and not new_pass:
            status = "🚫 信心過濾"; blocked += 1
        elif not old_pass and new_pass:
            status = "🆕 信心新增"; tp += 1
        else:
            status = "⛔ 雙拒絕"

        lines.append(f"  {status}  {fmt_score(d)}")

    if verbose and lines:
        print(f"\n{'─'*70}")
        print(f"  {path.name}  ({len(results)} 個候選，{len(orig_triggers)} 個原始觸發)")
        for l in lines:
            print(l)

    return dict(candidates=len(results), tp=tp, blocked=blocked,
                orig=len(orig_triggers), path=str(path.name))


# ── 主程式 ───────────────────────────────────────────────────

def main():
    root = Path(__file__).parent.parent / "tap_test_logs"
    if len(sys.argv) > 1:
        targets = [Path(a) for a in sys.argv[1:]]
    else:
        targets = sorted(root.glob("*.log"))

    summary = []
    for p in targets:
        if p.is_dir():
            for f in sorted(p.glob("*.log")):
                r = run_file(f)
                if r:
                    summary.append(r)
        else:
            r = run_file(p)
            if r:
                summary.append(r)

    if not summary:
        print("找不到含新格式 [tap][dim] 的 log 檔。")
        return

    print(f"\n{'═'*70}")
    print(f"  匯總  (門檻 CONF_TRIGGER={CONF_TRIGGER})")
    print(f"{'═'*70}")
    total_cand = sum(r["candidates"] for r in summary)
    total_pass = sum(r["tp"] for r in summary)
    total_blocked = sum(r["blocked"] for r in summary)
    total_orig = sum(r["orig"] for r in summary)
    print(f"  共 {len(summary)} 個 log，{total_cand} 個三連敲候選")
    print(f"  原始觸發（節奏 >= 0.65）: {total_orig}")
    print(f"  信心評分通過            : {total_pass}")
    print(f"  信心評分過濾掉          : {total_blocked}")
    if total_orig > 0:
        block_rate = total_blocked / (total_pass + total_blocked) * 100
        print(f"  過濾率                  : {block_rate:.1f}%")
    print()


if __name__ == "__main__":
    main()
