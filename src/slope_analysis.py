"""
DEM傾斜解析スクリプト（県別対応）
SRTM 1arc-second (~30m) DEMデータから傾斜角を計算し、
再生可能エネルギー不適地（傾斜15度以上）をマスクする。

使い方:
    python slope_analysis.py -p tochigi
    python slope_analysis.py -p chiba --dem-dir /path/to/hgt
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.transform import from_bounds
from rasterio.features import geometry_mask
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, get_land_dir, get_pref_config

# SRTM1 (1 arc-second) のサイズ
SRTM1_SIZE = 3601

# 傾斜しきい値（度）: これ以上は再エネ不適地
SLOPE_THRESHOLD_DEG = 15.0

# 傾斜区分（度）
SLOPE_CLASSES = [
    (0, 3, "0-3度（平坦）"),
    (3, 8, "3-8度（緩傾斜）"),
    (8, 15, "8-15度（中傾斜）"),
    (15, 30, "15-30度（急傾斜）"),
    (30, 90, "30度以上（険峻）"),
]


def read_srtm_hgt(filepath: Path) -> np.ndarray:
    """SRTM HGT ファイルを読み込む (big-endian int16, 3601x3601)"""
    data = np.fromfile(filepath, dtype=">i2").reshape(SRTM1_SIZE, SRTM1_SIZE)
    return data.astype(np.float32)


def tile_bounds(tile_name: str) -> tuple:
    """タイル名からバウンディングボックスを返す (west, south, east, north)"""
    lat = int(tile_name[1:3])
    lon = int(tile_name[4:7])
    if tile_name[0] == "S":
        lat = -lat
    if tile_name[3] == "W":
        lon = -lon
    return (lon, lat, lon + 1, lat + 1)


def mosaic_srtm(dem_dir: Path, srtm_tiles: list) -> tuple:
    """
    複数SRTMタイルをモザイクして結合する。
    Returns: (mosaic_array, transform, crs_wkt)
    """
    all_bounds = [tile_bounds(t) for t in srtm_tiles]
    min_lon = min(b[0] for b in all_bounds)
    min_lat = min(b[1] for b in all_bounds)
    max_lon = max(b[2] for b in all_bounds)
    max_lat = max(b[3] for b in all_bounds)

    lon_tiles = sorted(set(b[0] for b in all_bounds))
    lat_tiles = sorted(set(b[1] for b in all_bounds), reverse=True)

    n_lat = len(lat_tiles)
    n_lon = len(lon_tiles)

    tile_pixels = SRTM1_SIZE - 1
    total_rows = n_lat * tile_pixels + 1
    total_cols = n_lon * tile_pixels + 1
    mosaic = np.full((total_rows, total_cols), np.nan, dtype=np.float32)

    for tile_name in srtm_tiles:
        filepath = dem_dir / f"{tile_name}.hgt"
        if not filepath.exists():
            print(f"  WARNING: {filepath} が見つかりません。スキップします。")
            continue

        data = read_srtm_hgt(filepath)
        data[data == -32768] = np.nan

        bounds = tile_bounds(tile_name)
        col_idx = lon_tiles.index(bounds[0])
        row_idx = lat_tiles.index(bounds[1])

        r0 = row_idx * tile_pixels
        c0 = col_idx * tile_pixels
        mosaic[r0 : r0 + SRTM1_SIZE, c0 : c0 + SRTM1_SIZE] = data

    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, total_cols, total_rows)
    return mosaic, transform, "EPSG:4326"


def compute_slope(dem: np.ndarray, transform) -> np.ndarray:
    """DEMから傾斜角（度）を計算する。"""
    res_x = transform.a
    res_y = abs(transform.e)

    center_lat = transform.f + transform.e * (dem.shape[0] / 2)
    lat_rad = np.radians(center_lat)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(lat_rad)

    cell_size_x = res_x * m_per_deg_lon
    cell_size_y = res_y * m_per_deg_lat

    dy, dx = np.gradient(dem, cell_size_y, cell_size_x)
    slope_rad = np.arctan(np.sqrt(dx**2 + dy**2))
    slope_deg = np.degrees(slope_rad)

    return slope_deg


def clip_to_prefecture(
    data: np.ndarray, transform, crs: str, pref: str, land_dir: Path
) -> tuple:
    """県の行政境界でクリップする"""
    cfg = get_pref_config(pref)
    code = cfg["code"]
    name_ja = cfg["name_ja"]

    # Try multiple possible boundary file paths
    boundary_candidates = [
        land_dir / "admin_boundary" / f"N03-20240101_{code}.shp",
        land_dir / "admin_boundary" / f"N03-20240101_{code}.geojson",
    ]
    # Also search recursively for .shp or .geojson in admin_boundary
    admin_dir = land_dir / "admin_boundary"
    if admin_dir.exists():
        for f in admin_dir.rglob("*.shp"):
            if f not in boundary_candidates:
                boundary_candidates.append(f)
        for f in admin_dir.rglob("*.geojson"):
            if f not in boundary_candidates:
                boundary_candidates.append(f)

    boundary_shp = None
    for candidate in boundary_candidates:
        if candidate.exists():
            boundary_shp = candidate
            break

    if boundary_shp is None:
        print(f"WARNING: {name_ja}境界ファイルが見つかりません。クリップせずに出力します。")
        return data, transform

    print(f"  {name_ja}境界を読み込み: {boundary_shp}")
    gdf = gpd.read_file(boundary_shp)
    if gdf.crs and str(gdf.crs) != crs:
        gdf = gdf.to_crs(crs)

    pref_geom = unary_union(gdf.geometry)
    pref_bounds = pref_geom.bounds

    inv_transform = ~transform
    col_min, row_max = inv_transform * (pref_bounds[0], pref_bounds[1])
    col_max, row_min = inv_transform * (pref_bounds[2], pref_bounds[3])
    row_min = max(0, int(np.floor(row_min)) - 1)
    row_max = min(data.shape[0], int(np.ceil(row_max)) + 1)
    col_min = max(0, int(np.floor(col_min)) - 1)
    col_max = min(data.shape[1], int(np.ceil(col_max)) + 1)

    clipped = data[row_min:row_max, col_min:col_max].copy()
    new_transform = transform * rasterio.Affine.translation(col_min, row_min)

    mask = geometry_mask(
        [pref_geom],
        out_shape=clipped.shape,
        transform=new_transform,
        invert=False,
    )
    clipped[mask] = np.nan

    return clipped, new_transform


def compute_area_stats(slope: np.ndarray, transform) -> dict:
    """傾斜区分別の面積統計を計算"""
    res_x = abs(transform.a)
    res_y = abs(transform.e)
    center_lat = transform.f + transform.e * (slope.shape[0] / 2)
    lat_rad = np.radians(center_lat)
    pixel_area_m2 = (res_x * 111_320 * np.cos(lat_rad)) * (res_y * 111_320)
    pixel_area_km2 = pixel_area_m2 / 1e6

    valid = ~np.isnan(slope)
    total_valid = valid.sum()
    total_area = total_valid * pixel_area_km2

    stats = {}
    print("\n===== 傾斜区分別 面積統計 =====")
    print(f"  総有効面積: {total_area:.1f} km2")
    print(f"  ピクセル解像度: 約 {np.sqrt(pixel_area_m2):.1f} m")
    print("-" * 50)

    steep_area = 0.0
    for low, high, label in SLOPE_CLASSES:
        mask = valid & (slope >= low) & (slope < high)
        count = mask.sum()
        area = count * pixel_area_km2
        pct = (count / total_valid * 100) if total_valid > 0 else 0
        print(f"  {label:20s}: {area:8.1f} km2  ({pct:5.1f}%)")
        stats[label] = {"area_km2": area, "percent": pct}
        if low >= SLOPE_THRESHOLD_DEG:
            steep_area += area

    print("-" * 50)
    steep_pct = (steep_area / total_area * 100) if total_area > 0 else 0
    print(f"  傾斜15度以上（不適地）: {steep_area:.1f} km2  ({steep_pct:.1f}%)")
    print(f"  傾斜15度未満（適地候補）: {total_area - steep_area:.1f} km2  ({100 - steep_pct:.1f}%)")
    print("=" * 50)

    return stats


def save_geotiff(data: np.ndarray, transform, crs: str, output_path: Path):
    """GeoTIFF形式で保存"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=np.nan,
        compress="deflate",
    ) as dst:
        dst.write(data, 1)
    print(f"  保存完了: {output_path}")
    print(f"  サイズ: {data.shape[1]} x {data.shape[0]} pixels")


def main():
    parser = argparse.ArgumentParser(description="DEM傾斜解析（県別対応）")
    parser.add_argument(
        "--prefecture", "-p",
        type=str,
        default="tochigi",
        choices=list(PREFECTURES.keys()),
        help="対象都道府県 (default: tochigi)",
    )
    parser.add_argument(
        "--dem-dir",
        type=Path,
        default=None,
        help="SRTM HGTファイルのディレクトリ (default: data/{pref}/land/dem)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="出力GeoTIFFのパス (default: data/{pref}/land/{pref}_slope.tif)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=SLOPE_THRESHOLD_DEG,
        help=f"傾斜しきい値（度） (default: {SLOPE_THRESHOLD_DEG})",
    )
    args = parser.parse_args()

    pref = args.prefecture
    cfg = get_pref_config(pref)
    name_ja = cfg["name_ja"]
    srtm_tiles = cfg["srtm_tiles"]
    land_dir = get_land_dir(pref)

    dem_dir = args.dem_dir or (land_dir / "dem")
    output_path = args.output or (land_dir / f"{pref}_slope.tif")

    print("=" * 50)
    print(f"{name_ja} DEM傾斜解析")
    print("=" * 50)

    # 1. DEMモザイク
    print(f"\n[1/5] SRTMタイルの読み込みとモザイク...")
    print(f"  タイル: {srtm_tiles}")
    print(f"  DEMディレクトリ: {dem_dir}")
    missing = [t for t in srtm_tiles if not (dem_dir / f"{t}.hgt").exists()]
    if missing:
        print(f"  ERROR: 以下のファイルが見つかりません: {missing}")
        print(f"  まず download_land_data.py -p {pref} でDEMをダウンロードしてください。")
        sys.exit(1)

    dem, transform, crs = mosaic_srtm(dem_dir, srtm_tiles)
    print(f"  モザイクサイズ: {dem.shape[1]} x {dem.shape[0]} pixels")
    print(f"  標高範囲: {np.nanmin(dem):.0f} - {np.nanmax(dem):.0f} m")

    # 2. 傾斜計算
    print("\n[2/5] 傾斜角の計算...")
    slope = compute_slope(dem, transform)
    print(f"  傾斜範囲: {np.nanmin(slope):.1f} - {np.nanmax(slope):.1f} 度")

    # 3. 県域でクリップ
    print(f"\n[3/5] {name_ja}範囲でクリップ...")
    slope_clipped, clip_transform = clip_to_prefecture(slope, transform, crs, pref, land_dir)
    valid_count = (~np.isnan(slope_clipped)).sum()
    print(f"  クリップ後サイズ: {slope_clipped.shape[1]} x {slope_clipped.shape[0]} pixels")
    print(f"  有効ピクセル数: {valid_count:,}")

    # 4. GeoTIFF保存
    print("\n[4/5] 結果をGeoTIFFで保存...")
    save_geotiff(slope_clipped, clip_transform, crs, output_path)

    # 5. 面積統計
    print("\n[5/5] 傾斜区分別の面積統計...")
    compute_area_stats(slope_clipped, clip_transform)

    print("\n完了!")


if __name__ == "__main__":
    main()
