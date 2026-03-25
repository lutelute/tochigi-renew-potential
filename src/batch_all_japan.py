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
    # 全国実行 (5m, レジューム対応)
    python src/batch_all_japan.py --resolution 5 --resume

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


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {}


def save_checkpoint(cp: dict):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(
        json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def update_progress(pref: str, step: str, status: str, detail: str = ""):
    """進捗ファイルを更新 (外部からtailで監視可能)"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {pref:25s} | {step:15s} | {status:10s} | {detail}\n"
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
    log.info("  Running: %s", " ".join(cmd))
    log.info("  Log: %s", step_log)

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
            log.info("  [OK] %s/%s completed", pref, step)
            return True
        else:
            log.error("  [FAIL] %s/%s returncode=%d", pref, step, result.returncode)
            # ログの最後の数行を表示
            try:
                lines = step_log.read_text(encoding="utf-8").strip().split("\n")
                for line in lines[-5:]:
                    log.error("    | %s", line)
            except Exception:
                pass
            return False
    except subprocess.TimeoutExpired:
        log.error("  [TIMEOUT] %s/%s (>2h)", pref, step)
        return False
    except Exception as e:
        log.error("  [ERROR] %s/%s: %s", pref, step, e)
        return False


def process_prefecture(pref: str, resolution: int, checkpoint: dict,
                       skip_tiles: bool) -> bool:
    """1県を全ステップ処理。チェックポイント更新。"""
    cfg = PREFECTURES[pref]
    name_ja = cfg["name_ja"]

    completed_steps = checkpoint.get(pref, {}).get("completed_steps", [])

    log.info("=" * 60)
    log.info("Processing: %s (%s)", pref, name_ja)
    log.info("  Resolution: %dm", resolution)
    log.info("  Completed steps: %s", completed_steps)
    log.info("=" * 60)

    update_progress(pref, "START", "started", f"resolution={resolution}m")

    all_ok = True
    for step in STEPS:
        if step in completed_steps:
            log.info("  [SKIP] %s (already completed)", step)
            update_progress(pref, step, "skipped", "already completed")
            continue

        log.info("[%s] Step: %s", pref, step)
        update_progress(pref, step, "running")

        success = run_step(pref, step, resolution, skip_tiles)

        if success:
            completed_steps.append(step)
            checkpoint.setdefault(pref, {})["completed_steps"] = completed_steps
            checkpoint[pref]["last_update"] = datetime.now().isoformat()
            checkpoint[pref]["resolution"] = resolution
            save_checkpoint(checkpoint)
            update_progress(pref, step, "completed")
        else:
            update_progress(pref, step, "FAILED")
            log.error("  Step %s failed for %s. Moving to next prefecture.", step, pref)
            checkpoint.setdefault(pref, {})["failed_step"] = step
            checkpoint[pref]["last_update"] = datetime.now().isoformat()
            save_checkpoint(checkpoint)
            all_ok = False
            break  # このステップ失敗 → 次の県へ

        # Overpass API レート制限対策
        if step == "osm_land_use":
            log.info("  Waiting 60s for Overpass API rate limit...")
            time.sleep(60)

    if all_ok:
        checkpoint.setdefault(pref, {})["status"] = "completed"
        checkpoint[pref]["last_update"] = datetime.now().isoformat()
        save_checkpoint(checkpoint)
        update_progress(pref, "ALL", "completed", f"all {len(STEPS)} steps done")

    return all_ok


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

    log.info("=" * 60)
    log.info("全国再エネポテンシャル バッチ計算")
    log.info("  Resolution: %dm", args.resolution)
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
            log.info("Starting from: %s (%d prefectures remaining)", args.start_from, len(pref_list))
        except ValueError:
            log.error("%s not in target list", args.start_from)
            sys.exit(1)

    # 進捗ヘッダー書き込み
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"Batch started: {datetime.now().isoformat()}\n")
        f.write(f"Resolution: {args.resolution}m | Prefectures: {len(pref_list)}\n")
        f.write(f"{'='*80}\n")

    # 実行
    total = len(pref_list)
    completed = 0
    failed = []
    start_time = time.time()

    for i, pref in enumerate(pref_list):
        # 既に完了済みならスキップ
        if args.resume and checkpoint.get(pref, {}).get("status") == "completed":
            log.info("[%d/%d] %s: already completed, skipping", i + 1, total, pref)
            completed += 1
            continue

        log.info("[%d/%d] Starting %s...", i + 1, total, pref)
        ok = process_prefecture(pref, args.resolution, checkpoint, args.skip_tiles)

        if ok:
            completed += 1
        else:
            failed.append(pref)

        elapsed = time.time() - start_time
        rate = elapsed / (i + 1)
        remaining = rate * (total - i - 1)
        log.info(
            "Progress: %d/%d completed, %d failed | "
            "Elapsed: %.0fmin | ETA: %.0fmin",
            completed, total, len(failed),
            elapsed / 60, remaining / 60,
        )

    # 最終サマリー
    elapsed_total = time.time() - start_time
    log.info("=" * 60)
    log.info("BATCH COMPLETE")
    log.info("  Total: %d | Completed: %d | Failed: %d", total, completed, len(failed))
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
