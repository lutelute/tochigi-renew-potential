#!/usr/bin/env python3
"""
全国47都道府県 バッチオーケストレーター (v3: 2フェーズ方式)

Phase 1: 並列で各県の download → extract_grid → slope を実行 (外部API不要)
Phase 2: Overpass API を排他制御で順次実行 (osm_land_use)
Phase 3: 並列で raster_score を実行

各フェーズ内で失敗した県はスキップし、最後にリトライキューに回す。
Overpass API が不安定でも Phase 1/3 は完走する。

Usage:
    python src/batch_all_japan.py --resolution 5 --resume --workers 2
    python src/batch_all_japan.py -p fukui --resolution 30
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, PROJECT_ROOT

# ── ログ設定 ──────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = LOG_DIR / f"batch_{timestamp}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── チェックポイント ─────────────────────────────────────────
CHECKPOINT_FILE = PROJECT_ROOT / "data" / "batch_checkpoint.json"
PROGRESS_FILE = PROJECT_ROOT / "data" / "batch_progress.txt"

_checkpoint_lock = threading.Lock()
_progress_lock = threading.Lock()


def load_checkpoint() -> dict:
    with _checkpoint_lock:
        if CHECKPOINT_FILE.exists():
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return {}


def save_checkpoint(cp: dict):
    with _checkpoint_lock:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_FILE.write_text(
            json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def update_progress(pref: str, step: str, status: str, detail: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {pref:25s} | {step:15s} | {status:10s} | {detail}\n"
    with _progress_lock:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(line)


def mark_step_done(cp: dict, pref: str, step: str, resolution: int):
    with _checkpoint_lock:
        entry = cp.setdefault(pref, {"completed_steps": []})
        if step not in entry.get("completed_steps", []):
            entry.setdefault("completed_steps", []).append(step)
        entry["last_update"] = datetime.now().isoformat()
        entry["resolution"] = resolution
    save_checkpoint(cp)


def is_step_done(cp: dict, pref: str, step: str) -> bool:
    with _checkpoint_lock:
        return step in cp.get(pref, {}).get("completed_steps", [])


def mark_completed(cp: dict, pref: str):
    with _checkpoint_lock:
        cp.setdefault(pref, {})["status"] = "completed"
        cp[pref]["last_update"] = datetime.now().isoformat()
    save_checkpoint(cp)


# ── ステップ実行 ─────────────────────────────────────────────
def run_step(pref: str, step: str, resolution: int) -> bool:
    """各ステップをsubprocessで実行。成功=True"""
    src_dir = PROJECT_ROOT / "src"
    python = sys.executable

    env = os.environ.copy()
    if "ALL_JAPAN_GRID_DIR" not in env:
        candidate = Path.home() / "All-Japan-Grid-ref" / "data"
        if candidate.exists():
            env["ALL_JAPAN_GRID_DIR"] = str(candidate)

    cmd_map = {
        "download": [python, str(src_dir / "download_land_data.py"), "-p", pref],
        "extract_grid": [python, str(src_dir / "extract_grid.py"), "-p", pref],
        "slope": [python, str(src_dir / "slope_analysis.py"), "-p", pref],
        "osm_land_use": [python, str(src_dir / "fetch_osm_land_use.py"), "-p", pref],
        "raster_score": [python, str(src_dir / "raster_score.py"), "-p", pref,
                         "--resolution", str(resolution), "--skip-tiles"],
    }

    cmd = cmd_map.get(step)
    if not cmd:
        log.error("Unknown step: %s", step)
        return False

    step_log = LOG_DIR / f"{pref}_{step}_{timestamp}.log"
    log.info("  [%s] Running %s", pref, step)

    try:
        with open(step_log, "w", encoding="utf-8") as flog:
            result = subprocess.run(
                cmd, stdout=flog, stderr=subprocess.STDOUT,
                timeout=7200, cwd=str(PROJECT_ROOT), env=env,
            )
        if result.returncode == 0:
            log.info("  [%s] OK: %s", pref, step)
            return True
        else:
            log.error("  [%s] FAIL: %s (rc=%d)", pref, step, result.returncode)
            try:
                lines = step_log.read_text(encoding="utf-8").strip().split("\n")
                for line in lines[-3:]:
                    log.error("    | %s", line)
            except Exception:
                pass
            return False
    except subprocess.TimeoutExpired:
        log.error("  [%s] TIMEOUT: %s (>2h)", pref, step)
        return False
    except Exception as e:
        log.error("  [%s] ERROR: %s: %s", pref, step, e)
        return False


# ── フェーズ実行 ─────────────────────────────────────────────
def run_phase_parallel(phase_name: str, pref_list: list, steps: list,
                       resolution: int, cp: dict, workers: int) -> list:
    """複数ステップを並列で実行。失敗した県のリストを返す。"""
    log.info("=" * 60)
    log.info("PHASE: %s (%d prefectures, %d workers)", phase_name, len(pref_list), workers)
    log.info("  Steps: %s", steps)
    log.info("=" * 60)

    failed = []

    def _process_one(pref):
        for step in steps:
            if is_step_done(cp, pref, step):
                log.info("  [%s] SKIP %s (done)", pref, step)
                update_progress(pref, step, "skipped", "already done")
                continue

            update_progress(pref, step, "running")
            ok = run_step(pref, step, resolution)
            if ok:
                mark_step_done(cp, pref, step, resolution)
                update_progress(pref, step, "completed")
            else:
                update_progress(pref, step, "FAILED")
                return False
        return True

    if workers <= 1:
        for pref in pref_list:
            if not _process_one(pref):
                failed.append(pref)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_one, p): p for p in pref_list}
            for future in as_completed(futures):
                pref = futures[future]
                try:
                    if not future.result():
                        failed.append(pref)
                except Exception as e:
                    log.exception("  [%s] Unhandled error: %s", pref, e)
                    failed.append(pref)

    log.info("PHASE %s done: %d OK, %d failed",
             phase_name, len(pref_list) - len(failed), len(failed))
    return failed


def run_phase_overpass(pref_list: list, resolution: int, cp: dict) -> list:
    """Overpass APIを順次実行（排他制御＋指数バックオフ）。
    失敗した県はキューに戻して最大3周リトライする。"""
    step = "osm_land_use"

    # 既に完了済みを除外
    remaining = [p for p in pref_list if not is_step_done(cp, p, step)]
    if not remaining:
        log.info("PHASE Overpass: all already done, skipping")
        return []

    log.info("=" * 60)
    log.info("PHASE: Overpass API (%d prefectures, sequential)", len(remaining))
    log.info("=" * 60)

    max_rounds = 3  # 全体を最大3周
    for round_num in range(max_rounds):
        if not remaining:
            break

        if round_num > 0:
            wait = 300 * round_num  # 2周目: 5分, 3周目: 10分
            log.info("  Round %d/%d: %d remaining, waiting %ds before retry round...",
                     round_num + 1, max_rounds, len(remaining), wait)
            time.sleep(wait)

        still_failed = []
        for i, pref in enumerate(remaining):
            log.info("  [Overpass %d/%d, round %d] %s",
                     i + 1, len(remaining), round_num + 1, pref)
            update_progress(pref, step, "running",
                            f"round {round_num + 1}/{max_rounds}")

            ok = run_step(pref, step, resolution)

            if ok:
                mark_step_done(cp, pref, step, resolution)
                update_progress(pref, step, "completed")
                # クールダウン: 成功後30秒待つ
                if i < len(remaining) - 1:
                    log.info("  [%s] Cooldown 30s...", pref)
                    time.sleep(30)
            else:
                update_progress(pref, step, "FAILED",
                                f"round {round_num + 1}/{max_rounds}")
                still_failed.append(pref)
                # 失敗時は長めに待つ (APIが回復するのを待つ)
                wait = min(120 * (2 ** round_num), 600)
                log.warning("  [%s] Overpass failed, waiting %ds...", pref, wait)
                time.sleep(wait)

        remaining = still_failed

    if remaining:
        log.error("  Overpass: %d prefectures failed after %d rounds: %s",
                  len(remaining), max_rounds, ", ".join(remaining))
    else:
        log.info("  Overpass: all completed successfully")

    return remaining


def main():
    parser = argparse.ArgumentParser(
        description="全国47都道府県 バッチ計算オーケストレーター (3フェーズ方式)"
    )
    parser.add_argument("--prefecture", "-p", default=None,
                        help="カンマ区切りで指定")
    parser.add_argument("--resolution", "-r", type=int, default=5)
    parser.add_argument("--workers", "-w", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-from", default=None)
    parser.add_argument("--skip-tiles", action="store_true", default=True)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    workers = max(1, args.workers)

    log.info("=" * 60)
    log.info("全国再エネポテンシャル バッチ計算 (3-phase)")
    log.info("  Resolution: %dm | Workers: %d", args.resolution, workers)
    log.info("  Log: %s", log_file)
    log.info("=" * 60)

    if args.reset and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        log.info("Checkpoint reset.")

    cp = load_checkpoint() if args.resume else {}

    # 対象県リスト
    if args.prefecture:
        pref_list = [p.strip() for p in args.prefecture.split(",")]
        for p in pref_list:
            if p not in PREFECTURES:
                log.error("Unknown: %s", p)
                sys.exit(1)
    else:
        pref_list = list(PREFECTURES.keys())

    if args.start_from:
        idx = pref_list.index(args.start_from)
        pref_list = pref_list[idx:]

    # 完了済みを除外
    if args.resume:
        already = [p for p in pref_list if cp.get(p, {}).get("status") == "completed"]
        pref_list = [p for p in pref_list if p not in already]
        if already:
            log.info("Skipping %d completed prefectures", len(already))

    total = len(pref_list)
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"Batch started: {datetime.now().isoformat()}\n")
        f.write(f"Resolution: {args.resolution}m | Workers: {workers}"
                f" | Prefectures: {total} (3-phase)\n")
        f.write(f"{'='*80}\n")

    start_time = time.time()

    # ── Phase 1: download + extract_grid + slope (並列, API不要) ──
    phase1_steps = ["download", "extract_grid", "slope"]
    phase1_failed = run_phase_parallel(
        "1-local", pref_list, phase1_steps, args.resolution, cp, workers
    )

    # Phase1 で失敗した県を除外して Phase2 に進む
    phase2_list = [p for p in pref_list if p not in phase1_failed]

    # ── Phase 2: osm_land_use (順次, Overpass API) ──
    phase2_failed = run_phase_overpass(phase2_list, args.resolution, cp)

    # Phase2 で失敗した県を除外して Phase3 に進む
    # (osm_land_useがないとraster_scoreの土地利用がデフォルト値になるが実行は可能)
    phase3_list = [p for p in phase2_list]  # osm失敗でもraster_scoreは実行

    # ── Phase 3: raster_score (並列) ──
    phase3_failed = run_phase_parallel(
        "3-raster", phase3_list, ["raster_score"], args.resolution, cp, workers
    )

    # 全ステップ完了した県をマーク
    all_steps = phase1_steps + ["osm_land_use", "raster_score"]
    for pref in pref_list:
        steps_done = cp.get(pref, {}).get("completed_steps", [])
        if all(s in steps_done for s in all_steps):
            mark_completed(cp, pref)

    # ── 最終サマリー ──
    elapsed = time.time() - start_time
    completed = sum(1 for p in pref_list
                    if cp.get(p, {}).get("status") == "completed")
    all_failed = set(phase1_failed) | set(phase2_failed) | set(phase3_failed)

    log.info("=" * 60)
    log.info("BATCH COMPLETE")
    log.info("  Total: %d | Completed: %d | Partial/Failed: %d",
             total, completed, len(all_failed))
    log.info("  Phase 1 (local) failed: %s", phase1_failed or "none")
    log.info("  Phase 2 (overpass) failed: %s", phase2_failed or "none")
    log.info("  Phase 3 (raster) failed: %s", phase3_failed or "none")
    log.info("  Elapsed: %.1f hours", elapsed / 3600)
    log.info("=" * 60)

    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"BATCH COMPLETE: {datetime.now().isoformat()}\n")
        f.write(f"Total: {total} | Completed: {completed}"
                f" | Failed: {len(all_failed)}\n")
        f.write(f"Elapsed: {elapsed/3600:.1f} hours\n")
        if all_failed:
            f.write(f"Failed: {', '.join(sorted(all_failed))}\n")
        f.write(f"{'='*80}\n")


if __name__ == "__main__":
    main()
