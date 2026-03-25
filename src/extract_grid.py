"""
電力系統データを All-Japan-Grid から抽出し、
空容量CSVと結合して GeoJSON として出力する。
全国47都道府県対応版
"""
import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

# config.py からインポート
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, get_grid_dir, get_pref_config

# パス設定 (環境変数で上書き可能)
ALL_JAPAN_GRID = Path(os.environ.get("ALL_JAPAN_GRID_DIR", "/tmp/All-Japan-Grid-ref/data"))


def load_area_geojson(grid_area: str, kind: str) -> gpd.GeoDataFrame:
    """電力エリアの GeoJSON を読み込む"""
    path = ALL_JAPAN_GRID / f"{grid_area}_{kind}.geojson"
    print(f"  Loading {path.name} ...", end=" ")
    gdf = gpd.read_file(path)
    print(f"{len(gdf)} features")
    return gdf


def filter_by_bbox(gdf: gpd.GeoDataFrame, bbox_tuple: tuple,
                   pref_name_ja: str, buffer_km: float = 5.0) -> gpd.GeoDataFrame:
    """バウンディングボックスでフィルタ (バッファ付き)"""
    xmin, ymin, xmax, ymax = bbox_tuple
    pref_bbox = box(xmin, ymin, xmax, ymax)
    # 度単位での近似バッファ (1度 ≈ 111km)
    buf_deg = buffer_km / 111.0
    bbox_buffered = pref_bbox.buffer(buf_deg)
    mask = gdf.geometry.intersects(bbox_buffered)
    filtered = gdf[mask].copy()
    print(f"  Filtered to {pref_name_ja} area: {len(filtered)} features")
    return filtered


def extract_voltage_kv(voltage_str) -> float:
    """電圧文字列からkV値を抽出"""
    if pd.isna(voltage_str) or voltage_str == "":
        return 0
    try:
        v = float(str(voltage_str).replace(",", ""))
        if v > 1000:  # V単位の場合
            return v / 1000
        return v
    except ValueError:
        return 0


def main():
    parser = argparse.ArgumentParser(description="電力系統データ抽出 (県別)")
    parser.add_argument("-p", "--prefecture", required=True,
                        choices=list(PREFECTURES.keys()),
                        help="対象都道府県")
    args = parser.parse_args()

    pref = args.prefecture
    cfg = get_pref_config(pref)
    grid_dir = get_grid_dir(pref)
    name_ja = cfg["name_ja"]
    bbox_tuple = cfg["bbox"]
    grid_area = cfg["grid_area"]

    print("=" * 60)
    print(f"{name_ja} 電力系統データ抽出")
    print("=" * 60)

    # 1. All-Japan-Grid からエリアデータ読み込み
    print(f"\n[1] All-Japan-Grid データ読み込み ({grid_area} エリア)")
    subs = load_area_geojson(grid_area, "substations")
    lines = load_area_geojson(grid_area, "lines")
    plants = load_area_geojson(grid_area, "plants")

    # 2. 県域でフィルタ
    print(f"\n[2] {name_ja}エリアでフィルタ")
    subs_f = filter_by_bbox(subs, bbox_tuple, name_ja)
    lines_f = filter_by_bbox(lines, bbox_tuple, name_ja)
    plants_f = filter_by_bbox(plants, bbox_tuple, name_ja)

    # 電圧をkVに変換
    if "voltage" in subs_f.columns:
        subs_f["voltage_kv"] = subs_f["voltage"].apply(extract_voltage_kv)
    if "voltage" in lines_f.columns:
        lines_f["voltage_kv"] = lines_f["voltage"].apply(extract_voltage_kv)

    # 3. 空容量CSV読み込み
    print("\n[3] 空容量CSV読み込み")
    cap_files = {
        "transmission_lines": "capacity_transmission_lines.csv",
        "substations": "capacity_substations.csv",
        "distribution": "capacity_distribution_substations.csv",
    }
    cap_data = {}
    for key, fname in cap_files.items():
        path = grid_dir / fname
        if path.exists():
            df = pd.read_csv(path, on_bad_lines="warn")
            print(f"  Loaded {fname}: {len(df)} rows")
            cap_data[key] = df
        else:
            print(f"  WARNING: {path} not found")
            cap_data[key] = pd.DataFrame()

    # 4. 統計表示
    print(f"\n[4] 抽出データ統計")
    print(f"\n  変電所: {len(subs_f)}")
    if len(subs_f) > 0 and "voltage_kv" in subs_f.columns:
        vc = subs_f["voltage_kv"].value_counts().sort_index(ascending=False)
        for v, c in vc.items():
            print(f"    {v:>6.0f} kV: {c} 箇所")

    print(f"\n  送電線: {len(lines_f)}")
    if len(lines_f) > 0 and "voltage_kv" in lines_f.columns:
        vc = lines_f["voltage_kv"].value_counts().sort_index(ascending=False)
        for v, c in vc.items():
            print(f"    {v:>6.0f} kV: {c} 本")

    print(f"\n  発電所: {len(plants_f)}")
    if len(plants_f) > 0 and "fuel_type" in plants_f.columns:
        fc = plants_f["fuel_type"].value_counts()
        for f, c in fc.items():
            print(f"    {f}: {c}")

    print(f"\n  空容量データ:")
    print(f"    送電線: {len(cap_data['transmission_lines'])} 行")
    print(f"    変電所: {len(cap_data['substations'])} 行")
    print(f"    配電用変電所: {len(cap_data['distribution'])} 行")

    # 5. GeoJSON 出力
    print("\n[5] GeoJSON 出力")
    grid_dir.mkdir(parents=True, exist_ok=True)

    subs_f.to_file(grid_dir / f"{pref}_substations.geojson", driver="GeoJSON")
    print(f"  -> {pref}_substations.geojson ({len(subs_f)} features)")

    lines_f.to_file(grid_dir / f"{pref}_lines.geojson", driver="GeoJSON")
    print(f"  -> {pref}_lines.geojson ({len(lines_f)} features)")

    plants_f.to_file(grid_dir / f"{pref}_plants.geojson", driver="GeoJSON")
    print(f"  -> {pref}_plants.geojson ({len(plants_f)} features)")

    print("\n完了!")


if __name__ == "__main__":
    main()
