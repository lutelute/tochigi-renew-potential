#!/usr/bin/env python3
"""
Fetch OSM land use data via Overpass API and rasterize to 30m GeoTIFF.

For each prefecture, queries Overpass for landuse/natural/building polygons,
maps OSM tags to score codes, and rasterizes to match the slope reference grid.

Usage:
    python src/fetch_osm_land_use.py --prefecture all
    python src/fetch_osm_land_use.py --prefecture tochigi
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM tag -> score code mapping
# Score codes match the reclassification in raster_score.py
TAG_SCORE = {
    # Building/urban -> 0 (建物用地)
    "building": 0,
    "landuse=residential": 0,
    "landuse=commercial": 0,
    "landuse=industrial": 0,
    "landuse=retail": 0,
    "landuse=railway": 0,
    # Forest -> 20 (森林)
    "landuse=forest": 20,
    "natural=wood": 20,
    # Agriculture -> 40 (農用地)
    "landuse=farmland": 40,
    "landuse=meadow": 40,
    "landuse=orchard": 40,
    "landuse=vineyard": 40,
    "landuse=paddy": 40,       # rice paddy (common in Japan)
    # Golf course -> 85 (ゴルフ場)
    "landuse=recreation_ground": 85,
    "leisure=golf_course": 85,
    # Wasteland -> 90 (荒地)
    "landuse=brownfield": 90,
    "landuse=greenfield": 90,
    "landuse=quarry": 90,
    # Water -> 0 (not suitable)
    "natural=water": 0,
    "landuse=reservoir": 0,
}


def get_score_for_element(tags: dict) -> int:
    """Determine score code from OSM tags. Returns score or -1 if unmapped."""
    # Check building first (highest priority for exclusion)
    if "building" in tags and tags["building"] != "no":
        return TAG_SCORE["building"]

    # Check leisure=golf_course
    if tags.get("leisure") == "golf_course":
        return TAG_SCORE["leisure=golf_course"]

    # Check landuse
    lu = tags.get("landuse", "")
    key = f"landuse={lu}"
    if key in TAG_SCORE:
        return TAG_SCORE[key]

    # Check natural
    nat = tags.get("natural", "")
    key = f"natural={nat}"
    if key in TAG_SCORE:
        return TAG_SCORE[key]

    return -1  # unmapped


def query_overpass(bbox, timeout=180):
    """Query Overpass API for land use data within bbox.

    bbox: (west, south, east, north)
    Returns list of (geometry, score) tuples.
    """
    import urllib.request
    import urllib.parse

    west, south, east, north = bbox
    bbox_str = f"{south},{west},{north},{east}"

    query = f"""
[out:json][timeout:{timeout}];
(
  way["landuse"]({bbox_str});
  relation["landuse"]({bbox_str});
  way["natural"="wood"]({bbox_str});
  way["natural"="water"]({bbox_str});
  way["building"]({bbox_str});
  way["leisure"="golf_course"]({bbox_str});
  relation["leisure"="golf_course"]({bbox_str});
);
out geom;
"""

    log.info("  Querying Overpass API for bbox %s ...", bbox_str)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(OVERPASS_URL, data=data)
    req.add_header("User-Agent", "tochigi-renew-potential/1.0")

    max_attempts = 12  # 最大12回リトライ (合計最大 ~1時間待機)
    for attempt in range(max_attempts):
        try:
            # リクエストオブジェクトは毎回新規作成 (再利用不可のため)
            req = urllib.request.Request(OVERPASS_URL, data=data)
            req.add_header("User-Agent", "japan-re-potential/1.0")
            with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            log.info("  Got %d elements from Overpass", len(result.get("elements", [])))
            return result
        except Exception as e:
            # 指数バックオフ: 30, 60, 120, 240, 300, 300, ... 秒 (上限5分)
            wait = min(30 * (2 ** attempt), 300)
            log.warning("  Overpass attempt %d/%d failed: %s — waiting %ds",
                        attempt + 1, max_attempts, e, wait)
            if attempt < max_attempts - 1:
                time.sleep(wait)
            else:
                raise


def elements_to_geometries(elements):
    """Convert Overpass elements to list of (shapely_geometry, score) tuples."""
    results = []

    for elem in elements:
        tags = elem.get("tags", {})
        score = get_score_for_element(tags)
        if score == -1:
            score = 70  # default for unmapped

        geom = None
        etype = elem.get("type")

        if etype == "way" and "geometry" in elem:
            coords = [(n["lon"], n["lat"]) for n in elem["geometry"]]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                try:
                    from shapely.geometry import Polygon
                    geom = Polygon(coords)
                except Exception:
                    pass
            elif len(coords) >= 4:
                # Try closing the ring
                coords.append(coords[0])
                try:
                    from shapely.geometry import Polygon
                    geom = Polygon(coords)
                except Exception:
                    pass

        elif etype == "relation" and "members" in elem:
            # Try to build multipolygon from relation members
            outer_rings = []
            for member in elem.get("members", []):
                if member.get("role") == "outer" and "geometry" in member:
                    coords = [(n["lon"], n["lat"]) for n in member["geometry"]]
                    if len(coords) >= 4:
                        if coords[0] != coords[-1]:
                            coords.append(coords[0])
                        try:
                            from shapely.geometry import Polygon
                            outer_rings.append(Polygon(coords))
                        except Exception:
                            pass
            if outer_rings:
                try:
                    geom = unary_union(outer_rings)
                except Exception:
                    pass

        if geom is not None and geom.is_valid and not geom.is_empty:
            results.append((geom, score))

    return results


def split_bbox(bbox, n_splits=2):
    """Split bbox into n_splits x n_splits sub-bboxes."""
    west, south, east, north = bbox
    lon_step = (east - west) / n_splits
    lat_step = (north - south) / n_splits

    sub_bboxes = []
    for i in range(n_splits):
        for j in range(n_splits):
            sub_bbox = (
                west + j * lon_step,
                south + i * lat_step,
                west + (j + 1) * lon_step,
                south + (i + 1) * lat_step,
            )
            sub_bboxes.append(sub_bbox)
    return sub_bboxes


def fetch_land_use_for_prefecture(pref: str):
    """Fetch OSM land use and rasterize for one prefecture."""
    cfg = PREFECTURES[pref]
    bbox = cfg["bbox"]

    # Split into sub-queries to avoid Overpass timeouts
    sub_bboxes = split_bbox(bbox, n_splits=3)  # 3x3 = 9 sub-queries
    log.info("Fetching OSM land use for %s (%d sub-queries)", pref, len(sub_bboxes))

    all_geom_scores = []
    for i, sub_bbox in enumerate(sub_bboxes):
        log.info("  Sub-query %d/%d: bbox=%s", i + 1, len(sub_bboxes), sub_bbox)
        try:
            result = query_overpass(sub_bbox, timeout=180)
            geom_scores = elements_to_geometries(result.get("elements", []))
            all_geom_scores.extend(geom_scores)
            log.info("  Got %d polygons from sub-query %d", len(geom_scores), i + 1)
        except Exception as e:
            log.error("  Failed sub-query %d: %s", i + 1, e)

        # Be polite to Overpass
        if i < len(sub_bboxes) - 1:
            time.sleep(5)

    log.info("Total polygons for %s: %d", pref, len(all_geom_scores))

    if not all_geom_scores:
        log.error("No OSM land use data for %s!", pref)
        return

    # Load reference grid from slope TIF
    slope_path = PROJECT_ROOT / "data" / pref / "land" / f"{pref}_slope.tif"
    with rasterio.open(slope_path) as ds:
        ref_transform = ds.transform
        ref_width = ds.width
        ref_height = ds.height
        ref_crs = ds.crs

    log.info("Reference grid: %d x %d, CRS=%s", ref_width, ref_height, ref_crs)

    # Rasterize with priority: lower score (more restrictive) wins
    # We rasterize each score level separately, with building/urban (0) last (highest priority)
    score_levels = sorted(set(s for _, s in all_geom_scores), reverse=True)
    log.info("Score levels to rasterize: %s", score_levels)

    # Start with default 70 (unmapped)
    raster = np.full((ref_height, ref_width), 70, dtype=np.uint8)

    for score_val in score_levels:
        shapes = [(g, score_val) for g, s in all_geom_scores if s == score_val]
        if not shapes:
            continue
        log.info("  Rasterizing %d polygons for score=%d", len(shapes), score_val)
        try:
            layer = rasterize(
                shapes,
                out_shape=(ref_height, ref_width),
                transform=ref_transform,
                fill=255,  # 255 = no data for this layer
                dtype=np.uint8,
                all_touched=True,
            )
            # Overwrite where this layer has data
            mask = layer != 255
            raster[mask] = layer[mask]
        except Exception as e:
            log.error("  Failed to rasterize score=%d: %s", score_val, e)

    # Write output
    out_dir = PROJECT_ROOT / "data" / pref / "land" / "land_use"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "osm_land_use.tif"

    with rasterio.open(
        out_path, "w",
        driver="GTiff",
        height=ref_height,
        width=ref_width,
        count=1,
        dtype="uint8",
        crs=ref_crs,
        transform=ref_transform,
        compress="deflate",
    ) as dst:
        dst.write(raster, 1)

    log.info("Wrote %s (%d x %d)", out_path, ref_width, ref_height)

    # Print statistics
    unique, counts = np.unique(raster, return_counts=True)
    total_px = ref_height * ref_width
    log.info("Score distribution for %s:", pref)
    for u, c in zip(unique, counts):
        log.info("  score=%d: %d pixels (%.1f%%)", u, c, c / total_px * 100)


def main():
    parser = argparse.ArgumentParser(description="Fetch OSM land use data")
    parser.add_argument(
        "--prefecture", "-p",
        default="all",
        choices=list(PREFECTURES.keys()) + ["all"],
    )
    args = parser.parse_args()

    prefs = list(PREFECTURES.keys()) if args.prefecture == "all" else [args.prefecture]

    for pref in prefs:
        try:
            fetch_land_use_for_prefecture(pref)
        except Exception:
            log.exception("FAILED: %s", pref)
            continue

    log.info("All done.")


if __name__ == "__main__":
    main()
