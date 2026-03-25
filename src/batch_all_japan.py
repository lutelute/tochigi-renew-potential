#!/usr/bin/env python3
"""
全国47都道府県 バッチオーケストレーター

チェックポイント機能付きで都道府県ごとに5ステップを順次実行:
  1. download (DEM + 国土数値情報)
  2. extract_grid (All-Japan-Grid から系統データ抽出)
  3. slope (傾斜解析)
  4. osm_land_use (Overpass API で土地利用取得)
  5. raster_score (ラスタースコア計算)

Usage:
    # 全国実行 (5m, 2並列, レジューム対応)
    python src/batch_all_japan.py --resolution 5 --resume --workers 2

    # 福井県のみテスト (30m)
    python src/batch_all_japan.py -p fukui --resolution 30

    # 特定の県から再開
    python src/batch_all_japan.py --resolution 5 --resume --start-from nagano
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

# ── 進捗ファイル (外部から監視用) ──────────────────────────────
PROGRESS_FILE = PROJECT_ROOT / "data" / "batch_progress.txt"

# ── スレッドセーフ用ロック ────────────────────────────────────
_checkpoint_lock = threading.Lock()
_progress_lock = threading.Lock()
_overpass_lock = threading.Lock()  # Overpass API 排他制御


def load_checkpoint() -> dict:
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
    """進捗ファイルを更新 (外部からtailで監視可能)"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {pref:25s} | {step:15s} | {status:10s} | {detail}\n"
    with _progress_lock:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(line)


# ── ステップ定義 ─────────────────────────────────────────────
STEPS = ["download", "extract_grid", "slope", "osm_land_use", "raster_score"]


def run_step(pref: str, step: str, resolution: int, skip_tiles: bool) -> bool:
    """各ステップをsubprocessで実行。成功=True"""
    src_dir = PROJECT_ROOT / "src"
    python = sys.executable

    env = os.environ.copy()
    # サーバーのAll-Japan-Gridパスを環境変数で設定
    if "ALL_JAPAN_GRID_DIR" not in env:
        candidate = Path.home() / "All-Japan-Grid-ref" / "data"
        if candidate.exists():
            env["ALL_JAPAN_GRID_DIR"] = str(candidate)

    if step == "download":
        cmd = [python, str(src_dir / "download_land_data.py"), "-p", pref]
    elif step == "extract_grid":
        cmd = [python, str(src_dir / "extract_grid.py"), "-p", pref]
    elif step == "slope":
        cmd = [python, str(src_dir / "slope_analysis.py"), "-p", pref]
    elif step == "osm_land_use":
        cmd = [python, str(src_dir / "fetch_osm_land_use.py"), "-p", pref]
    elif step == "raster_score":
        cmd = [python, str(src_dir / "raster_score.py"), "-p", pref,
               "--resolution", str(resolution), "--skip-tiles"]
    else:
        log.error("Unknown step: %s", step)
        return False

    step_log = LOG_DIR / f"{pref}_{step}_{timestamp}.log"
    log.info("  [%s] Running: %s", pref, " ".join(cmd))

    try:
        with open(step_log, "w", encoding="utf-8") as flog:
            result = subprocess.run(
                cmd,
                stdout=flog,
                stderr=subprocess.STDOUT,
                timeout=7200,  # 2時間タイムアウト
                cwd=str(PROJECT_ROOT),
                env=env,
            )
        if result.returncode == 0:
            log.info("  [%s] [OK] %s completed", pref, step)
            return True
        else:
            log.error("  [%s] [FAIL] %s returncode=%d", pref, step, result.returncode)
            try:
                lines = step_log.read_text(encoding="utf-8").strip().split("\n")
                for line in lines[-5:]:
                    log.error("    | %s", line)
            except Exception:
                pass
            return False
    except subprocess.TimeoutExpired:
        log.error("  [%s] [TIMEOUT] %s (>2h)", pref, step)
        return False
    except Exception as e:
        log.error("  [%s] [ERROR] %s: %s", pref, step, e)
        return False


def process_prefecture(pref: str, resolution: int, checkpoint: dict,
                       skip_tiles: bool) -> bool:
    """1県を全ステップ処理。チェックポイント更新。"""
    cfg = PREFECTURES[pref]
    name_ja = cfg["name_ja"]

    with _checkpoint_lock:
        completed_steps = list(checkpoint.get(pref, {}).get("completed_steps", []))

    log.info("=" * 60)
    log.info("Processing: %s (%s)", pref, name_ja)
    log.info("  Resolution: %dm", resolution)
    log.info("  Completed steps: %s", completed_steps)
    log.info("=" * 60)

    update_progress(pref, "START", "started", f"resolution={resolution}m")

    all_ok = True
    for step in STEPS:
        if step in completed_steps:
            log.info("  [%s] [SKIP] %s (already completed)", pref, step)
            update_progress(pref, step, "skipped", "already completed")
            continue

        log.info("[%s] Step: %s", pref, step)
        update_progress(pref, step, "running")

        # Overpass API は排他制御 + リトライ
        if step == "osm_land_use":
            success = _run_overpass_with_lock(pref, resolution, skip_tiles)
        else:
            success = run_step(pref, step, resolution, skip_tiles)

        if success:
            completed_steps.append(step)
            with _checkpoint_lock:
                checkpoint.setdefault(pref, {})["completed_steps"] = completed_steps
                checkpoint[pref]["last_update"] = datetime.now().isoformat()
                checkpoint[pref]["resolution"] = resolution
            save_checkpoint(checkpoint)
            update_progress(pref, step, "completed")
        else:
            update_progress(pref, step, "FAILED")
            log.error("  [%s] Step %s failed. Moving on.", pref, step)
            with _checkpoint_lock:
                checkpoint.setdefault(pref, {})["failed_step"] = step
                checkpoint[pref]["last_update"] = datetime.now().isoformat()
            save_checkpoint(checkpoint)
            all_ok = False
            break

    if all_ok:
        with _checkpoint_lock:
            checkpoint.setdefault(pref, {})["status"] = "completed"
            checkpoint[pref]["last_update"] = datetime.now().isoformat()
        save_checkpoint(checkpoint)
        update_progress(pref, "ALL", "completed", f"all {len(STEPS)} steps done")

    return all_ok


def _run_overpass_with_lock(pref: str, resolution: int, skip_tiles: bool) -> bool:
    """Overpass API呼び出しをロックで排他制御。失敗時はリトライ。"""
    max_retries = 3
    for attempt in range(max_retries):
        log.info("  [%s] Waiting for Overpass lock (attempt %d/%d)...",
                 pref, attempt + 1, max_retries)
        with _overpass_lock:
            log.info("  [%s] Overpass lock acquired", pref)
            success = run_step(pref, "osm_land_use", resolution, skip_tiles)
            if success:
                # ロック解放後にクールダウン (他ワーカーがすぐ叩かないように)
                log.info("  [%s] Overpass done, cooldown 30s...", pref)
                time.sleep(30)
                return True
            else:
                log.warning("  [%s] Overpass failed, cooldown 60s before retry...", pref)
                time.sleep(60)

    return False


def main():
    parser = argparse.ArgumentParser(
        description="全国47都道府県 バッチ計算オーケストレーター"
    )
    parser.add_argument(
        "--prefecture", "-p",
        default=None,
        help="特定の都道府県のみ実行 (カンマ区切りで複数指定可)",
    )
    parser.add_argument(
        "--resolution", "-r",
        type=int,
        default=5,
        help="計算解像度 [m] (default: 5)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=1,
        help="並列ワーカー数 (default: 1)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="チェックポイントから再開",
    )
    parser.add_argument(
        "--start-from",
        default=None,
        help="指定した都道府県から開始 (それより前はスキップ)",
    )
    parser.add_argument(
        "--skip-tiles",
        action="store_true",
        default=True,
        help="タイル生成をスキップ (default: True, サーバー計算時)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="チェックポイントをリセットして最初から",
    )
    args = parser.parse_args()

    workers = max(1, args.workers)

    log.info("=" * 60)
    log.info("全国再エネポテンシャル バッチ計算")
    log.info("  Resolution: %dm", args.resolution)
    log.info("  Workers: %d", workers)
    log.info("  Log file: %s", log_file)
    log.info("  Progress file: %s", PROGRESS_FILE)
    log.info("  Checkpoint: %s", CHECKPOINT_FILE)
    log.info("=" * 60)

    # チェックポイント読み込み
    if args.reset and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        log.info("Checkpoint reset.")

    checkpoint = load_checkpoint() if args.resume else {}

    # 対象県リスト
    if args.prefecture:
        pref_list = [p.strip() for p in args.prefecture.split(",")]
        for p in pref_list:
            if p not in PREFECTURES:
                log.error("Unknown prefecture: %s", p)
                sys.exit(1)
    else:
        pref_list = list(PREFECTURES.keys())

    # --start-from で開始位置を調整
    if args.start_from:
        if args.start_from not in PREFECTURES:
            log.error("Unknown prefecture for --start-from: %s", args.start_from)
            sys.exit(1)
        try:
            idx = pref_list.index(args.start_from)
            pref_list = pref_list[idx:]
            log.info("Starting from: %s (%d prefectures remaining)",
                     args.start_from, len(pref_list))
        except ValueError:
            log.error("%s not in target list", args.start_from)
            sys.exit(1)

    # 完了済みをフィルタ
    if args.resume:
        remaining = [p for p in pref_list
                     if checkpoint.get(p, {}).get("status") != "completed"]
        skipped = len(pref_list) - len(remaining)
        if skipped:
            log.info("Skipping %d already-completed prefectures", skipped)
        pref_list = remaining

    # 進捗ヘッダー書き込み
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"Batch started: {datetime.now().isoformat()}\n")
        f.write(f"Resolution: {args.resolution}m | Workers: {workers}"
                f" | Prefectures: {len(pref_list)}\n")
        f.write(f"{'='*80}\n")

    total = len(pref_list)
    completed = 0
    failed = []
    start_time = time.time()

    if workers == 1:
        # ── シングルワーカー (従来動作) ──────────────────────
        for i, pref in enumerate(pref_list):
            log.info("[%d/%d] Starting %s...", i + 1, total, pref)
            ok = process_prefecture(pref, args.resolution, checkpoint,
                                    args.skip_tiles)
            if ok:
                completed += 1
            else:
                failed.append(pref)

            elapsed = time.time() - start_time
            done_count = completed + len(failed)
            rate = elapsed / done_count if done_count else 0
            remaining_t = rate * (total - done_count)
            log.info(
                "Progress: %d/%d completed, %d failed | "
                "Elapsed: %.0fmin | ETA: %.0fmin",
                completed, total, len(failed),
                elapsed / 60, remaining_t / 60,
            )
    else:
        # ── マルチワーカー ───────────────────────────────────
        log.info("Starting %d parallel workers", workers)

        def _worker(pref):
            return pref, process_prefecture(
                pref, args.resolution, checkpoint, args.skip_tiles
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, p): p for p in pref_list}
            for future in as_completed(futures):
                pref = futures[future]
                try:
                    _, ok = future.result()
                except Exception as e:
                    log.exception("  [%s] Unhandled exception: %s", pref, e)
                    ok = False

                if ok:
                    completed += 1
                else:
                    failed.append(pref)

                elapsed = time.time() - start_time
                done_count = completed + len(failed)
                rate = elapsed / done_count if done_count else 0
                remaining_t = rate * (total - done_count)
                log.info(
                    "Progress: %d/%d completed, %d failed | "
                    "Elapsed: %.0fmin | ETA: %.0fmin",
                    completed, total, len(failed),
                    elapsed / 60, remaining_t / 60,
                )

    # 最終サマリー
    elapsed_total = time.time() - start_time
    log.info("=" * 60)
    log.info("BATCH COMPLETE")
    log.info("  Total: %d | Completed: %d | Failed: %d",
             total, completed, len(failed))
    log.info("  Elapsed: %.1f hours", elapsed_total / 3600)
    if failed:
        log.info("  Failed prefectures: %s", ", ".join(failed))
    log.info("=" * 60)

    # 進捗ファイルに最終サマリー
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"BATCH COMPLETE: {datetime.now().isoformat()}\n")
        f.write(f"Total: {total} | Completed: {completed} | Failed: {len(failed)}\n")
        f.write(f"Elapsed: {elapsed_total/3600:.1f} hours\n")
        if failed:
            f.write(f"Failed: {', '.join(failed)}\n")
        f.write(f"{'='*80}\n")


if __name__ == "__main__":
    main()
