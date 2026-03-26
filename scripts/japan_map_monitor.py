#!/usr/bin/env python3
"""
全国バッチ進捗 — ASCII日本地図モニター

Usage:
    python scripts/japan_map_monitor.py                    # 1回表示
    python scripts/japan_map_monitor.py --watch             # 60秒ごと自動更新
    python scripts/japan_map_monitor.py --local checkpoint.json  # ローカルファイル
"""

import json
import os
import subprocess
import sys
import time
import argparse
from datetime import datetime

# ANSI colors
G = "\033[92m"   # green (completed)
Y = "\033[93m"   # yellow (in-progress)
R = "\033[91m"   # red (failed)
C = "\033[96m"   # cyan (overpass phase)
W = "\033[97m"   # white
D = "\033[90m"   # dark gray (not started)
B = "\033[1m"    # bold
RST = "\033[0m"  # reset

# Prefecture display config: (key, short_label, row, col)
# Row/Col are grid positions for the ASCII map
MAP_LAYOUT = [
    # ── 北海道 ──
    ("hokkaido_soya",       "宗谷", 0, 30),
    ("hokkaido_rumoi",      "留萌", 1, 27),
    ("hokkaido_kamikawa",   "上川", 1, 30),
    ("hokkaido_okhotsk",    "ｵﾎｰﾂ", 1, 33),
    ("hokkaido_sorachi",    "空知", 2, 27),
    ("hokkaido_shiribeshi", "後志", 2, 24),
    ("hokkaido_ishikari",   "石狩", 2, 30),
    ("hokkaido_tokachi",    "十勝", 2, 33),
    ("hokkaido_iburi",      "胆振", 3, 27),
    ("hokkaido_hidaka",     "日高", 3, 30),
    ("hokkaido_kushiro",    "釧路", 3, 33),
    ("hokkaido_nemuro",     "根室", 3, 36),
    ("hokkaido_oshima",     "渡島", 4, 24),
    ("hokkaido_hiyama",     "檜山", 4, 27),

    # ── 東北 ──
    ("aomori",    "青森", 6, 24),
    ("akita",     "秋田", 7, 21),
    ("iwate",     "岩手", 7, 24),
    ("yamagata",  "山形", 8, 21),
    ("miyagi",    "宮城", 8, 24),
    ("fukushima", "福島", 9, 24),
    ("niigata",   "新潟", 9, 18),

    # ── 関東 ──
    ("gunma",    "群馬", 10, 18),
    ("tochigi",  "栃木", 10, 21),
    ("ibaraki",  "茨城", 10, 24),
    ("saitama",  "埼玉", 11, 18),
    ("tokyo",    "東京", 11, 21),
    ("chiba",    "千葉", 11, 24),
    ("kanagawa", "神奈", 12, 21),
    ("yamanashi","山梨", 12, 18),

    # ── 中部 ──
    ("toyama",   "富山", 10, 12),
    ("nagano",   "長野", 10, 15),
    ("ishikawa", "石川", 11, 9),
    ("fukui",    "福井", 12, 9),
    ("gifu",     "岐阜", 11, 12),
    ("aichi",    "愛知", 12, 12),
    ("shizuoka", "静岡", 12, 15),

    # ── 近畿 ──
    ("shiga",     "滋賀", 13, 12),
    ("mie",       "三重", 13, 15),
    ("kyoto",     "京都", 13, 9),
    ("osaka",     "大阪", 14, 9),
    ("hyogo",     "兵庫", 14, 6),
    ("nara",      "奈良", 14, 12),
    ("wakayama",  "和歌", 15, 9),

    # ── 中国 ──
    ("tottori",  "鳥取", 13, 3),
    ("shimane",  "島根", 13, 0),
    ("okayama",  "岡山", 14, 3),
    ("hiroshima","広島", 15, 0),
    ("yamaguchi","山口", 15, -3),

    # ── 四国 ──
    ("kagawa",     "香川", 15, 3),
    ("tokushima",  "徳島", 15, 6),
    ("ehime",      "愛媛", 16, 0),
    ("kochi",      "高知", 16, 3),

    # ── 九州 ──
    ("fukuoka",    "福岡", 16, -6),
    ("oita",       "大分", 16, -3),
    ("saga",       "佐賀", 17, -9),
    ("nagasaki",   "長崎", 17, -6),
    ("kumamoto",   "熊本", 17, -3),
    ("miyazaki",   "宮崎", 17, 0),
    ("kagoshima",  "鹿児", 18, -3),

    # ── 沖縄 ──
    ("okinawa",  "沖縄", 20, -9),
]


def get_checkpoint_ssh(server="pws-ubuntu-server@100.104.225.55"):
    """SSHでサーバーからcheckpointを取得"""
    cmd = [
        "ssh", "-o", "ConnectTimeout=8", server,
        "cat ~/projects/kanto-re-potential/data/batch_checkpoint.json 2>/dev/null"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    # Fallback to LAN
    cmd[3] = "pws-ubuntu-server@10.0.70.42"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def get_checkpoint_local(path):
    with open(path) as f:
        return json.load(f)


def classify(cp, key):
    """0=not started, 1=phase1, 2=overpass done, 3=all done(completed), -1=failed"""
    if cp is None:
        return 0
    entry = cp.get(key, {})
    steps = entry.get("completed_steps", [])
    all_steps = {"download", "extract_grid", "slope", "osm_land_use", "raster_score"}
    # 全5ステップ完了 or status=completed → 完了
    if all_steps.issubset(set(steps)) or entry.get("status") == "completed":
        return 3
    if "failed_step" in entry and entry.get("status") != "completed":
        return -1
    if "raster_score" in steps:
        return 3
    if "osm_land_use" in steps:
        return 2
    if len(steps) > 0:
        return 1
    return 0


def render_map(cp):
    # Grid: figure out bounds
    min_col = min(c for _, _, _, c in MAP_LAYOUT)
    max_col = max(c for _, _, _, c in MAP_LAYOUT) + 4  # label width
    min_row = min(r for _, _, r, _ in MAP_LAYOUT)
    max_row = max(r for _, _, r, _ in MAP_LAYOUT)

    col_offset = -min_col + 2  # shift so min_col starts at 2

    # Build grid of characters (wide enough for labels)
    width = max_col - min_col + 8
    height = max_row - min_row + 1
    # Use a list of lists of characters
    grid = [[" "] * (width * 3) for _ in range(height)]

    stats = {0: 0, 1: 0, 2: 0, 3: 0, -1: 0}

    colored_cells = []  # (row, col_start, col_end, colored_string)

    for key, label, row, col in MAP_LAYOUT:
        status = classify(cp, key)
        stats[status] += 1

        adj_col = (col + col_offset) * 2  # 2 chars per grid unit

        if status == 3:
            marker = f"{G}{B}{label}{RST}"
        elif status == 2:
            marker = f"{C}{label}{RST}"
        elif status == 1:
            marker = f"{Y}{label}{RST}"
        elif status == -1:
            marker = f"{R}{label}{RST}"
        else:
            marker = f"{D}{label}{RST}"

        colored_cells.append((row - min_row, adj_col, marker))

    # Build output lines
    lines = []

    # Title
    now = datetime.now().strftime("%H:%M:%S")
    lines.append(f"{B}{'='*56}{RST}")
    lines.append(f"{B}  全国再エネポテンシャル 5m計算 進捗マップ  {D}({now}){RST}")
    lines.append(f"{B}{'='*56}{RST}")
    lines.append("")

    # Render map rows
    for r in range(height):
        # Collect cells for this row, sorted by column
        row_cells = sorted(
            [(c, m) for rr, c, m in colored_cells if rr == r],
            key=lambda x: x[0]
        )
        if not row_cells:
            lines.append("")
            continue

        line = ""
        pos = 0
        for col_pos, marker in row_cells:
            if col_pos > pos:
                line += " " * (col_pos - pos)
            line += marker
            # Actual display width of label is 4 chars (2 fullwidth)
            pos = col_pos + 4
        lines.append("  " + line)

    lines.append("")

    # Legend & stats
    total = sum(stats.values())
    lines.append(f"  {G}{B}██{RST} 完了({stats[3]})  "
                 f"{C}██{RST} OSM済({stats[2]})  "
                 f"{Y}██{RST} Phase1({stats[1]})  "
                 f"{D}██{RST} 未着手({stats[0]})  "
                 f"{R}██{RST} 失敗({stats[-1]})")
    lines.append("")

    pct = stats[3] / total * 100 if total else 0
    bar_len = 40
    filled = int(bar_len * stats[3] / total) if total else 0
    bar = f"{G}{'█' * filled}{D}{'░' * (bar_len - filled)}{RST}"
    lines.append(f"  [{bar}] {stats[3]}/{total} ({pct:.0f}%)")
    lines.append(f"{B}{'='*56}{RST}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ASCII Japan map progress monitor")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh every 60s")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval (s)")
    parser.add_argument("--local", type=str, default=None, help="Local checkpoint JSON")
    args = parser.parse_args()

    while True:
        if args.local:
            cp = get_checkpoint_local(args.local)
        else:
            cp = get_checkpoint_ssh()

        # Clear screen
        os.system("clear" if os.name != "nt" else "cls")

        if cp is None:
            print(f"{R}サーバーに接続できません。リトライ中...{RST}")
        else:
            print(render_map(cp))

        if not args.watch:
            break

        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n{D}Bye!{RST}")
            break


if __name__ == "__main__":
    main()
