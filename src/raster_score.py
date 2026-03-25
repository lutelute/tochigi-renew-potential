#!/usr/bin/env python3
"""
Raster-based score calculation for renewable energy potential.
Supports configurable resolution (5m / 10m / 30m).

Usage:
    python src/raster_score.py --prefecture tochigi                    # 30m (default)
    python src/raster_score.py --prefecture tochigi --resolution 5     # 5m (server向け)
    python src/raster_score.py --prefecture all --resolution 10        # 10m 全県
"""

import argparse
import logging
import subprocess
import sys
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, rasterize
from rasterio.merge import merge
from rasterio.transform import from_bounds
from rasterio.warp import reproject, calculate_default_transform
from scipy.ndimage import distance_transform_edt

# ── project imports ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, WEIGHTS, PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── colour ramp (score -> RGBA) ──────────────────────────────────
COLOUR_MAP = {
    # (r, g, b, a)  — 0 is transparent
    0: (0, 0, 0, 0),
    1: (220, 20, 60, 180),     # crimson  1-19
    20: (255, 140, 0, 180),    # darkorange 20-39
    40: (218, 165, 32, 180),   # goldenrod 40-59
    60: (34, 139, 34, 180),    # forestgreen 60-79
    80: (0, 100, 0, 180),      # darkgreen 80-100
}


def score_to_rgba(score_arr: np.ndarray) -> np.ndarray:
    """Convert uint8 score array to (4, H, W) RGBA array."""
    h, w = score_arr.shape
    rgba = np.zeros((4, h, w), dtype=np.uint8)
    # 0 stays transparent
    bands = [
        ((1, 20), (220, 20, 60, 180)),
        ((20, 40), (255, 140, 0, 180)),
        ((40, 60), (218, 165, 32, 180)),
        ((60, 80), (34, 139, 34, 180)),
        ((80, 101), (0, 100, 0, 180)),
    ]
    for (lo, hi), (r, g, b, a) in bands:
        mask = (score_arr >= lo) & (score_arr < hi)
        rgba[0][mask] = r
        rgba[1][mask] = g
        rgba[2][mask] = b
        rgba[3][mask] = a
    return rgba


# ── helper: reference grid ────────────────────────────────────────
def load_reference_grid(pref: str, resolution_m: int = 30):
    """Return (transform, width, height, crs, bounds) at the requested resolution.

    resolution_m=30 uses the slope TIF natively.
    resolution_m=5 or 10 creates a denser grid covering the same extent.
    """
    slope_path = PROJECT_ROOT / "data" / pref / "land" / f"{pref}_slope.tif"
    with rasterio.open(slope_path) as ds:
        native_transform = ds.transform
        native_w, native_h = ds.width, ds.height
        crs = ds.crs
        bounds = ds.bounds

    # Native resolution in degrees (SRTM ~30m → ~0.000278°)
    native_res_x = abs(native_transform.a)
    native_res_y = abs(native_transform.e)

    # Approximate native resolution in metres
    centre_lat = (bounds.top + bounds.bottom) / 2
    m_per_deg = 111320 * np.cos(np.radians(centre_lat))
    native_m = native_res_x * m_per_deg  # ≈ 30m

    if resolution_m >= native_m * 0.9:
        # Use native grid as-is
        log.info("Using native reference grid (%d x %d, ~%.0fm)", native_w, native_h, native_m)
        return native_transform, native_w, native_h, crs, bounds

    # Build a finer grid at the requested resolution
    scale = native_m / resolution_m
    new_w = int(np.ceil(native_w * scale))
    new_h = int(np.ceil(native_h * scale))
    new_transform = from_bounds(bounds.left, bounds.bottom, bounds.right, bounds.top, new_w, new_h)

    log.info(
        "Custom reference grid: %d x %d pixels (~%dm) — %.1fx denser than native",
        new_w, new_h, resolution_m, scale,
    )
    return new_transform, new_w, new_h, crs, bounds


def _resample_to_grid(src_path: Path, transform, width, height, crs,
                      resampling=Resampling.bilinear, band=1) -> np.ndarray:
    """Read a single-band raster and reproject/resample to the reference grid."""
    with rasterio.open(src_path) as ds:
        src_data = ds.read(band)
        src_transform = ds.transform
        src_crs = ds.crs
        if (ds.width == width and ds.height == height
                and ds.transform.almost_equals(transform)):
            return src_data  # already matches
    dst = np.zeros((height, width), dtype=src_data.dtype)
    reproject(
        source=src_data,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=transform,
        dst_crs=crs,
        resampling=resampling,
    )
    return dst


# ── score: slope ─────────────────────────────────────────────────
def compute_score_slope(pref: str, transform, width, height, crs) -> np.ndarray:
    """Read slope TIF, resample to reference grid, reclassify to 0-100."""
    slope_path = PROJECT_ROOT / "data" / pref / "land" / f"{pref}_slope.tif"
    log.info("  slope: reading %s", slope_path)
    slope = _resample_to_grid(slope_path, transform, width, height, crs,
                              resampling=Resampling.bilinear)
    slope = np.nan_to_num(slope, nan=0.0)
    score = np.zeros((height, width), dtype=np.uint8)
    score[slope < 3] = 100
    score[(slope >= 3) & (slope < 5)] = 85
    score[(slope >= 5) & (slope < 8)] = 70
    score[(slope >= 8) & (slope < 15)] = 40
    score[(slope >= 15) & (slope < 30)] = 15
    score[slope >= 30] = 0
    return score


# ── score: elevation ─────────────────────────────────────────────
def compute_score_elevation(pref: str, transform, width, height, crs, bounds) -> np.ndarray:
    """Mosaic DEM HGT files (or derive from slope fallback) -> elevation score."""
    dem_dir = PROJECT_ROOT / "data" / pref / "land" / "dem"
    hgt_files = sorted(dem_dir.glob("*.hgt")) if dem_dir.exists() else []

    if not hgt_files:
        log.warning("  elevation: no HGT files for %s, using latitude-based fallback", pref)
        # simple latitude-based fallback: higher lat = higher elev estimate
        cfg = PREFECTURES[pref]
        rows = np.arange(height)
        # latitude for each row
        lats = bounds.top - rows * abs(transform.e)
        # crude estimate: map lat range to 0-500m
        lat_min, lat_max = cfg["bbox"][1], cfg["bbox"][3]
        elev_est = ((lats - lat_min) / (lat_max - lat_min) * 400).astype(np.float32)
        elev = np.broadcast_to(elev_est[:, None], (height, width)).copy()
    else:
        log.info("  elevation: mosaicking %d HGT files", len(hgt_files))
        datasets = [rasterio.open(str(f)) for f in hgt_files]
        mosaic, mosaic_transform = merge(datasets)
        for ds in datasets:
            ds.close()
        mosaic = mosaic[0]  # single band

        # reproject to reference grid
        elev = np.zeros((height, width), dtype=np.float32)
        reproject(
            source=mosaic,
            destination=elev,
            src_transform=mosaic_transform,
            src_crs="EPSG:4326",
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.bilinear,
        )

    elev = np.nan_to_num(elev, nan=0.0)
    score = np.zeros((height, width), dtype=np.uint8)
    score[elev <= 100] = 100
    score[(elev > 100) & (elev <= 300)] = 80
    score[(elev > 300) & (elev <= 500)] = 60
    score[(elev > 500) & (elev <= 1000)] = 30
    score[elev > 1000] = 10
    return score


# ── helper: distance score via rasterization + EDT ───────────────
def _distance_score(
    geometries,
    transform, width, height, crs,
    breakpoints: list[tuple[float, int]],
) -> np.ndarray:
    """
    Rasterize geometries, compute distance transform, apply piecewise scoring.
    breakpoints: [(distance_m, score), ...] sorted ascending by distance.
    Last entry means >= that distance gets that score.
    """
    if len(geometries) == 0:
        log.warning("    no geometries to rasterize, returning default 50")
        return np.full((height, width), 50, dtype=np.uint8)

    # rasterize: 1 where geometry present, 0 elsewhere
    burned = rasterize(
        [(g, 1) for g in geometries],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=True,
    )

    # EDT on inverted mask (True = no geometry = background for EDT)
    # pixel size in meters: approximate using centre latitude
    centre_lat = transform.f - abs(transform.e) * height / 2
    lat_rad = np.radians(centre_lat)
    m_per_deg_lon = 111320 * np.cos(lat_rad)
    m_per_deg_lat = 110540
    pixel_dx = abs(transform.a) * m_per_deg_lon
    pixel_dy = abs(transform.e) * m_per_deg_lat

    dist_pixels = distance_transform_edt(burned == 0, sampling=[pixel_dy, pixel_dx])
    dist_m = dist_pixels.astype(np.float32)  # already in metres thanks to sampling

    # apply piecewise linear scoring
    score = np.zeros((height, width), dtype=np.float32)
    bp = sorted(breakpoints, key=lambda x: x[0])
    for i in range(len(bp)):
        d, s = bp[i]
        if i == 0:
            mask = dist_m <= d
            score[mask] = s
        else:
            d_prev, s_prev = bp[i - 1]
            mask = (dist_m > d_prev) & (dist_m <= d)
            # linear interpolation
            frac = (dist_m[mask] - d_prev) / (d - d_prev)
            score[mask] = s_prev + frac * (s - s_prev)
    # beyond last breakpoint
    d_last, s_last = bp[-1]
    score[dist_m > d_last] = s_last

    return np.clip(score, 0, 100).astype(np.uint8)


# ── score: grid distance ────────────────────────────────────────
def compute_score_grid_dist(pref: str, transform, width, height, crs) -> np.ndarray:
    """Distance to 154kV+ transmission lines."""
    lines_path = PROJECT_ROOT / "data" / pref / "grid" / f"{pref}_lines.geojson"
    log.info("  grid_dist: reading %s", lines_path)
    gdf = gpd.read_file(lines_path)

    # filter 154kV+
    if "voltage_kv" in gdf.columns:
        gdf = gdf[gdf["voltage_kv"] >= 154]
    log.info("  grid_dist: %d lines >= 154kV", len(gdf))

    geometries = gdf.geometry.tolist()
    breakpoints = [
        (0, 100),
        (1000, 90),
        (3000, 70),
        (5000, 50),
        (10000, 20),
        (20000, 0),
    ]
    return _distance_score(geometries, transform, width, height, crs, breakpoints)


# ── score: substation distance ───────────────────────────────────
def compute_score_sub_dist(pref: str, transform, width, height, crs) -> np.ndarray:
    """Distance to 66kV+ substations."""
    subs_path = PROJECT_ROOT / "data" / pref / "grid" / f"{pref}_substations.geojson"
    log.info("  sub_dist: reading %s", subs_path)
    gdf = gpd.read_file(subs_path)

    if "voltage_kv" in gdf.columns:
        gdf = gdf[gdf["voltage_kv"] >= 66]
    log.info("  sub_dist: %d substations >= 66kV", len(gdf))

    # use centroids for polygon geometries
    geometries = [g.centroid if g.geom_type in ("Polygon", "MultiPolygon") else g for g in gdf.geometry]
    breakpoints = [
        (0, 100),
        (2000, 80),
        (5000, 50),
        (10000, 20),
        (20000, 0),
    ]
    return _distance_score(geometries, transform, width, height, crs, breakpoints)


# ── score: distribution line distance (66kV) ────────────────────
def compute_score_dist_line(pref: str, transform, width, height, crs) -> np.ndarray:
    """Distance to 66kV+ distribution lines (66kV <= voltage < 154kV)."""
    lines_path = PROJECT_ROOT / "data" / pref / "grid" / f"{pref}_lines.geojson"
    log.info("  dist_line: reading %s", lines_path)
    gdf = gpd.read_file(lines_path)

    # filter 66kV+ but < 154kV (distribution lines)
    if "voltage_kv" in gdf.columns:
        gdf = gdf[(gdf["voltage_kv"] >= 66) & (gdf["voltage_kv"] < 154)]
    log.info("  dist_line: %d lines 66-154kV", len(gdf))

    geometries = gdf.geometry.tolist()
    breakpoints = [
        (0, 100),
        (1000, 85),
        (3000, 60),
        (5000, 35),
        (10000, 10),
        (15000, 0),
    ]
    return _distance_score(geometries, transform, width, height, crs, breakpoints)


# ── score: land use ──────────────────────────────────────────────
def compute_score_land_use(pref: str, transform, width, height, crs) -> np.ndarray:
    """Read land use score. Prefer OSM rasterized TIF, fall back to L03-b TIFs."""
    lu_dir = PROJECT_ROOT / "data" / pref / "land" / "land_use"

    # Prefer OSM land use (already scored, same CRS/grid as slope)
    osm_path = lu_dir / "osm_land_use.tif" if lu_dir.exists() else None
    if osm_path is not None and osm_path.exists():
        log.info("  land_use: using OSM data %s", osm_path)
        with rasterio.open(osm_path) as ds:
            score = ds.read(1)
        # The OSM TIF is already on the reference grid with score values
        if score.shape == (height, width):
            return score
        # If shape differs, reproject
        log.info("  land_use: reprojecting OSM data to reference grid")
        with rasterio.open(osm_path) as ds:
            src_data = ds.read(1)
            src_transform = ds.transform
            src_crs = ds.crs
        dst = np.zeros((height, width), dtype=np.uint8)
        reproject(
            source=src_data,
            destination=dst,
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.nearest,
        )
        return dst

    # Fall back to L03-b TIFs (国土数値情報)
    tifs = sorted(lu_dir.glob("L03-b*.tif")) if lu_dir.exists() else []

    if not tifs:
        log.warning("  land_use: no TIF for %s, using default 70", pref)
        return np.full((height, width), 70, dtype=np.uint8)

    log.info("  land_use: mosaicking %d L03-b TIFs", len(tifs))
    datasets = [rasterio.open(str(f)) for f in tifs]
    mosaic_data, mosaic_transform = merge(datasets)
    mosaic_crs = datasets[0].crs
    for ds in datasets:
        ds.close()
    lu_raw = mosaic_data[0]

    # reproject to reference grid
    lu = np.zeros((height, width), dtype=np.uint8)
    reproject(
        source=lu_raw.astype(np.uint8),
        destination=lu,
        src_transform=mosaic_transform,
        src_crs=mosaic_crs,
        dst_transform=transform,
        dst_crs=crs,
        resampling=Resampling.nearest,
    )

    # reclassify
    remap = {
        60: 90,    # 荒地
        160: 85,   # ゴルフ場
        100: 80,   # その他
        10: 40,    # 田
        20: 40,    # 農地
        50: 20,    # 森林
        70: 0,     # 建物用地
        91: 0,     # 道路
        92: 0,     # 鉄道
        110: 0,    # 河川
    }
    score = np.full((height, width), 70, dtype=np.uint8)  # default for unmapped codes
    for code, s in remap.items():
        score[lu == code] = s
    return score


# ── total score ──────────────────────────────────────────────────
def compute_total_score(scores: dict) -> np.ndarray:
    """Weighted sum of individual scores."""
    w = WEIGHTS
    total = (
        scores["slope"].astype(np.float32) * w["slope"]
        + scores["grid_dist"].astype(np.float32) * w["grid_distance"]
        + scores["dist_line"].astype(np.float32) * w["distribution_line_distance"]
        + scores["sub_dist"].astype(np.float32) * w["substation_distance"]
        + scores["land_use"].astype(np.float32) * w["land_use"]
        + scores["elevation"].astype(np.float32) * w["elevation"]
        + 50.0 * w["road_distance"]     # default road score
        + 80.0 * w["protection"]         # default protection score
    )
    return np.clip(total, 0, 100).astype(np.uint8)


# ── output helpers ───────────────────────────────────────────────
def write_score_tif(arr: np.ndarray, path: Path, transform, crs):
    """Write uint8 score array as GeoTIFF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
        compress="deflate",
    ) as dst:
        dst.write(arr, 1)
    log.info("  wrote %s (%d x %d)", path, arr.shape[1], arr.shape[0])


def write_rgba_tif(score_arr: np.ndarray, path: Path, transform, crs):
    """Write RGBA coloured GeoTIFF."""
    rgba = score_to_rgba(score_arr)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=score_arr.shape[0],
        width=score_arr.shape[1],
        count=4,
        dtype="uint8",
        crs=crs,
        transform=transform,
        compress="deflate",
    ) as dst:
        dst.write(rgba)
    log.info("  wrote RGBA %s", path)


def generate_tiles(rgba_tif: Path, tiles_dir: Path, zoom="7-14"):
    """Run gdal2tiles.py to generate XYZ tiles."""
    if tiles_dir.exists():
        shutil.rmtree(tiles_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gdal2tiles.py",
        "-z", zoom,
        "-w", "none",
        "--xyz",
        "-r", "near",
        str(rgba_tif),
        str(tiles_dir),
    ]
    log.info("  generating tiles: %s -> %s", rgba_tif.name, tiles_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("  gdal2tiles failed: %s", result.stderr[:500])
    else:
        log.info("  tiles generated OK")


# ── main pipeline per prefecture ─────────────────────────────────
def process_prefecture(pref: str, resolution_m: int = 30, skip_tiles: bool = False):
    log.info("=" * 60)
    log.info("Processing %s @ %dm resolution", pref.upper(), resolution_m)
    log.info("=" * 60)

    transform, width, height, crs, bounds = load_reference_grid(pref, resolution_m)
    log.info("Reference grid: %d x %d pixels (%d MP), CRS=%s",
             width, height, width * height // 1_000_000, crs)

    # resolution suffix for output files (30m = no suffix for backwards compat)
    res_suffix = f"_{resolution_m}m" if resolution_m != 30 else ""

    output_dir = PROJECT_ROOT / "output" / pref
    docs_dir = PROJECT_ROOT / "docs" / pref

    scores = {}

    # 1) slope
    log.info("[1/6] Computing slope score...")
    scores["slope"] = compute_score_slope(pref, transform, width, height, crs)

    # 2) elevation
    log.info("[2/6] Computing elevation score...")
    scores["elevation"] = compute_score_elevation(pref, transform, width, height, crs, bounds)

    # 3) grid distance (154kV+)
    log.info("[3/6] Computing grid distance score...")
    scores["grid_dist"] = compute_score_grid_dist(pref, transform, width, height, crs)

    # 4) distribution line distance (66-154kV)
    log.info("[4/6] Computing distribution line distance score...")
    scores["dist_line"] = compute_score_dist_line(pref, transform, width, height, crs)

    # 5) substation distance
    log.info("[5/6] Computing substation distance score...")
    scores["sub_dist"] = compute_score_sub_dist(pref, transform, width, height, crs)

    # 6) land use
    log.info("[6/6] Computing land use score...")
    scores["land_use"] = compute_score_land_use(pref, transform, width, height, crs)

    # total
    log.info("Computing total score...")
    scores["total"] = compute_total_score(scores)

    # Mask outside prefecture boundary
    log.info("Masking to prefecture boundary...")
    admin_dir = PROJECT_ROOT / "data" / pref / "land" / "admin_boundary"
    shp_files = list(admin_dir.rglob("*.shp")) if admin_dir.exists() else []
    if shp_files:
        admin = gpd.read_file(shp_files[0])
        boundary_geom = admin.union_all()
        outside_mask = geometry_mask(
            [boundary_geom], transform=transform,
            out_shape=(height, width), invert=False
        )
        for name in scores:
            scores[name][outside_mask] = 0  # 0 = transparent in RGBA
        log.info("  Masked %d pixels outside boundary", outside_mask.sum())
    else:
        log.warning("  No admin boundary found, skipping mask")

    # Zoom levels based on resolution
    if resolution_m <= 5:
        zoom = "7-17"
    elif resolution_m <= 10:
        zoom = "7-16"
    else:
        zoom = "7-14"

    # Write individual score TIFs
    score_names = ["total", "slope", "grid_dist", "dist_line", "sub_dist", "land_use", "elevation"]
    for name in score_names:
        tif_name = f"score_{name}{res_suffix}.tif"
        write_score_tif(scores[name], output_dir / tif_name, transform, crs)

    # Generate RGBA TIFs and tiles for each score
    for name in score_names:
        rgba_path = output_dir / f"score_{name}{res_suffix}_rgba.tif"
        write_rgba_tif(scores[name], rgba_path, transform, crs)

        if not skip_tiles:
            tile_dir_name = f"tiles_{name}{res_suffix}" if name != "total" else f"tiles{res_suffix}"
            tiles_path = docs_dir / tile_dir_name
            generate_tiles(rgba_path, tiles_path, zoom=zoom)

            # Also generate separate tiles_total directory
            if name == "total":
                tiles_total_path = docs_dir / f"tiles_total{res_suffix}"
                if tiles_total_path != tiles_path:
                    generate_tiles(rgba_path, tiles_total_path, zoom=zoom)

    if not skip_tiles:
        # Also copy total score TIF to docs for web access
        total_tif_src = output_dir / f"score_total{res_suffix}.tif"
        total_tif_dst = docs_dir / f"mesh_score{res_suffix}.tif"
        shutil.copy2(total_tif_src, total_tif_dst)
        log.info("  copied %s -> docs/%s/%s", total_tif_src.name, pref, total_tif_dst.name)
    else:
        log.info("  Tile generation skipped (--skip-tiles)")

    log.info("DONE: %s @ %dm", pref, resolution_m)


# ── CLI ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Raster score calculation (configurable resolution: 5m / 10m / 30m)"
    )
    parser.add_argument(
        "--prefecture", "-p",
        default="all",
        choices=list(PREFECTURES.keys()) + ["all"],
        help="Prefecture to process (default: all)",
    )
    parser.add_argument(
        "--resolution", "-r",
        type=int,
        default=30,
        help="Raster resolution in metres (default: 30). Use 5 for high-res server computation.",
    )
    parser.add_argument(
        "--skip-tiles",
        action="store_true",
        help="Skip tile generation (gdal2tiles). Useful when GDAL is not installed.",
    )
    args = parser.parse_args()

    resolution_m = args.resolution
    if resolution_m < 1:
        parser.error("Resolution must be >= 1m")

    # Warn about memory for high-res
    if resolution_m <= 5:
        log.warning(
            "Resolution %dm is very high — expect large memory usage. "
            "Recommended to run on the compute server.",
            resolution_m,
        )

    prefs = list(PREFECTURES.keys()) if args.prefecture == "all" else [args.prefecture]

    for pref in prefs:
        try:
            process_prefecture(pref, resolution_m=resolution_m, skip_tiles=args.skip_tiles)
        except Exception:
            log.exception("FAILED: %s", pref)
            continue

    log.info("All done.")


if __name__ == "__main__":
    main()
