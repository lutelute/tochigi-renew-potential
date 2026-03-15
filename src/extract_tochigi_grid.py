"""
栃木県の電力系統データを All-Japan-Grid から抽出し、
空容量CSVと結合して GeoJSON として出力する。
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, box

# パス設定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARENT_DIR = PROJECT_ROOT.parent
ALL_JAPAN_GRID = Path("/tmp/All-Japan-Grid-ref/data")
DATA_DIR = PROJECT_ROOT / "data"
GRID_DIR = DATA_DIR / "grid"
CAPACITY_DIR = PARENT_DIR / "data"  # 抽出済みCSV

# 栃木県のバウンディングボックス (少し余裕を持たせる)
TOCHIGI_BBOX = box(139.3, 36.2, 140.2, 37.2)


def load_tokyo_geojson(kind: str) -> gpd.GeoDataFrame:
    """東京電力エリアの GeoJSON を読み込む"""
    path = ALL_JAPAN_GRID / f"tokyo_{kind}.geojson"
    print(f"  Loading {path.name} ...", end=" ")
    gdf = gpd.read_file(path)
    print(f"{len(gdf)} features")
    return gdf


def filter_tochigi(gdf: gpd.GeoDataFrame, buffer_km: float = 5.0) -> gpd.GeoDataFrame:
    """栃木県バウンディングボックスでフィルタ (バッファ付き)"""
    # 度単位での近似バッファ (1度 ≈ 111km)
    buf_deg = buffer_km / 111.0
    bbox_buffered = TOCHIGI_BBOX.buffer(buf_deg)
    mask = gdf.geometry.intersects(bbox_buffered)
    filtered = gdf[mask].copy()
    print(f"  Filtered to Tochigi area: {len(filtered)} features")
    return filtered


def load_capacity_csv(name: str) -> pd.DataFrame:
    """空容量CSVを読み込む"""
    path = CAPACITY_DIR / name
    if not path.exists():
        print(f"  WARNING: {path} not found")
        return pd.DataFrame()
    df = pd.read_csv(path, on_bad_lines="warn")
    print(f"  Loaded {name}: {len(df)} rows")
    return df


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
    print("=" * 60)
    print("栃木県 電力系統データ抽出")
    print("=" * 60)

    # 1. All-Japan-Grid から東京エリアデータ読み込み
    print("\n[1] All-Japan-Grid データ読み込み")
    subs = load_tokyo_geojson("substations")
    lines = load_tokyo_geojson("lines")
    plants = load_tokyo_geojson("plants")

    # 2. 栃木県でフィルタ
    print("\n[2] 栃木県エリアでフィルタ")
    subs_t = filter_tochigi(subs)
    lines_t = filter_tochigi(lines)
    plants_t = filter_tochigi(plants)

    # 電圧をkVに変換
    subs_t["voltage_kv"] = subs_t["voltage"].apply(extract_voltage_kv)
    lines_t["voltage_kv"] = lines_t["voltage"].apply(extract_voltage_kv)

    # 3. 空容量CSV読み込み
    print("\n[3] 空容量CSV読み込み")
    cap_trans = load_capacity_csv("transmission_lines.csv")
    cap_subs = load_capacity_csv("substations.csv")
    cap_dist = load_capacity_csv("distribution_substations.csv")

    # 4. 統計表示
    print("\n[4] 抽出データ統計")
    print(f"\n  変電所: {len(subs_t)}")
    if len(subs_t) > 0:
        vc = subs_t["voltage_kv"].value_counts().sort_index(ascending=False)
        for v, c in vc.items():
            print(f"    {v:>6.0f} kV: {c} 箇所")

    print(f"\n  送電線: {len(lines_t)}")
    if len(lines_t) > 0:
        vc = lines_t["voltage_kv"].value_counts().sort_index(ascending=False)
        for v, c in vc.items():
            print(f"    {v:>6.0f} kV: {c} 本")

    print(f"\n  発電所: {len(plants_t)}")
    if len(plants_t) > 0 and "fuel_type" in plants_t.columns:
        fc = plants_t["fuel_type"].value_counts()
        for f, c in fc.items():
            print(f"    {f}: {c}")

    print(f"\n  空容量データ:")
    print(f"    送電線: {len(cap_trans)} 行")
    print(f"    変電所: {len(cap_subs)} 行")
    print(f"    配電用変電所: {len(cap_dist)} 行")

    # 5. GeoJSON 出力
    print("\n[5] GeoJSON 出力")
    GRID_DIR.mkdir(parents=True, exist_ok=True)

    subs_t.to_file(GRID_DIR / "tochigi_substations.geojson", driver="GeoJSON")
    print(f"  -> tochigi_substations.geojson ({len(subs_t)} features)")

    lines_t.to_file(GRID_DIR / "tochigi_lines.geojson", driver="GeoJSON")
    print(f"  -> tochigi_lines.geojson ({len(lines_t)} features)")

    plants_t.to_file(GRID_DIR / "tochigi_plants.geojson", driver="GeoJSON")
    print(f"  -> tochigi_plants.geojson ({len(plants_t)} features)")

    # 空容量CSVもコピー
    for name in ["transmission_lines.csv", "substations.csv", "distribution_substations.csv"]:
        src = CAPACITY_DIR / name
        dst = GRID_DIR / f"capacity_{name}"
        if src.exists():
            pd.read_csv(src).to_csv(dst, index=False)
            print(f"  -> capacity_{name}")

    print("\n完了!")


if __name__ == "__main__":
    main()
