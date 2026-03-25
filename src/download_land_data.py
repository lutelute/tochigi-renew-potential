"""
土地利用・規制データをダウンロード（県別対応）
国土数値情報から取得
"""
import argparse
import os
import zipfile
from pathlib import Path
import urllib.request
import ssl

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PREFECTURES, get_land_dir, get_pref_config

# SSL証明書検証を緩和（国土数値情報サーバー用）
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 土地利用メッシュの2次メッシュコード (県ごとに主要なものを指定)
LAND_USE_MESHES = {
    "tochigi": ["5540"],
    "chiba": ["5340", "5440"],
    "ibaraki": ["5440", "5540"],
}


def build_downloads(pref: str, code: str) -> dict:
    """県コードに基づいてダウンロードURLを構築"""
    cfg = get_pref_config(pref)
    name_ja = cfg["name_ja"]

    downloads = {
        # 行政区域 - 2024年版
        "admin_boundary": {
            "url": f"https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_{code}_GML.zip",
            "desc": f"行政区域 ({name_ja})",
        },
        # 森林地域 (2015年版 = A13-15)
        "forest": {
            "url": f"https://nlftp.mlit.go.jp/ksj/gml/data/A13/A13-15/A13-15_{code}_GML.zip",
            "desc": f"森林地域 ({name_ja})",
        },
        # 農業地域
        "agriculture": {
            "url": f"https://nlftp.mlit.go.jp/ksj/gml/data/A12/A12-15/A12-15_{code}_GML.zip",
            "desc": f"農業地域 ({name_ja})",
        },
    }

    # 土地利用メッシュ (GeoTIFF)
    # NOTE: 国土数値情報の土地利用GeoTIFFダウンロードURLは変更が多い。
    # 404の場合は手動で https://nlftp.mlit.go.jp/ksj/ からダウンロードしてください。
    meshes = LAND_USE_MESHES.get(pref, [])
    for i, mesh in enumerate(meshes):
        key = f"land_use_{mesh}" if len(meshes) > 1 else "land_use"
        downloads[key] = {
            "url": f"https://nlftp.mlit.go.jp/ksj/gml/data/L03-b-r/L03-b-r-21_{mesh}-jgd_GeoTIFF.zip",
            "desc": f"土地利用メッシュ ({mesh})",
        }

    return downloads


def download_file(url: str, dest: Path, desc: str) -> bool:
    if dest.exists():
        print(f"  [SKIP] {desc} - already exists")
        return True

    print(f"  [DL] {desc}")
    print(f"       {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            data = resp.read()
        dest.write_bytes(data)
        size_mb = len(data) / (1024 * 1024)
        print(f"       -> {dest.name} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"       ERROR: {e}")
        return False


def extract_zip(zip_path: Path, extract_dir: Path):
    if not zip_path.exists():
        return
    print(f"  [EXTRACT] {zip_path.name}")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_dir)
    # List extracted files
    for f in sorted(extract_dir.rglob("*")):
        if f.is_file() and f.suffix in [".shp", ".geojson", ".tif", ".gml", ".xml"]:
            print(f"       {f.relative_to(extract_dir)}")


def download_srtm(pref: str, land_dir: Path) -> bool:
    """SRTM DEMタイルをダウンロード"""
    cfg = get_pref_config(pref)
    tiles = cfg["srtm_tiles"]
    dem_dir = land_dir / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)

    all_ok = True
    for tile in tiles:
        hgt_path = dem_dir / f"{tile}.hgt"
        if hgt_path.exists():
            print(f"  [SKIP] DEM {tile} - already exists")
            continue

        # Try multiple SRTM download sources
        urls = [
            f"https://s3.amazonaws.com/elevation-tiles-prod/skadi/{tile[:3]}/{tile}.hgt.gz",
        ]

        downloaded = False
        for url in urls:
            gz_path = dem_dir / f"{tile}.hgt.gz"
            print(f"  [DL] DEM {tile}")
            print(f"       {url}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, context=ctx, timeout=180) as resp:
                    data = resp.read()
                gz_path.write_bytes(data)
                size_mb = len(data) / (1024 * 1024)
                print(f"       -> {gz_path.name} ({size_mb:.1f} MB)")

                # Decompress
                import gzip
                print(f"  [EXTRACT] {gz_path.name}")
                with gzip.open(gz_path, 'rb') as f_in:
                    hgt_path.write_bytes(f_in.read())
                gz_path.unlink()
                print(f"       -> {hgt_path.name}")
                downloaded = True
                break
            except Exception as e:
                print(f"       ERROR: {e}")
                if gz_path.exists():
                    gz_path.unlink()

        if not downloaded:
            print(f"  FAILED: Could not download DEM tile {tile}")
            all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="土地利用・規制データ ダウンロード")
    parser.add_argument(
        "--prefecture", "-p",
        type=str,
        default="tochigi",
        choices=list(PREFECTURES.keys()),
        help="対象都道府県 (default: tochigi)",
    )
    parser.add_argument(
        "--skip-dem",
        action="store_true",
        help="DEMダウンロードをスキップ",
    )
    args = parser.parse_args()

    pref = args.prefecture
    cfg = get_pref_config(pref)
    code = cfg["code"]
    name_ja = cfg["name_ja"]
    land_dir = get_land_dir(pref)
    land_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"{name_ja} 土地利用・規制データ ダウンロード")
    print("=" * 60)

    # 国土数値情報データ
    downloads = build_downloads(pref, code)
    for key, info in downloads.items():
        dest = land_dir / f"{key}.zip"
        extract_dir = land_dir / key

        success = download_file(info["url"], dest, info["desc"])
        if success and dest.exists():
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                extract_zip(dest, extract_dir)
            except Exception as e:
                print(f"  Extract error: {e}")

    # SRTM DEM
    if not args.skip_dem:
        print(f"\n--- SRTM DEMタイルのダウンロード ---")
        download_srtm(pref, land_dir)

    print(f"\n完了!")
    print(f"データ保存先: {land_dir}")


if __name__ == "__main__":
    main()
