"""
栃木県の土地利用・規制データをダウンロード
国土数値情報から取得
"""
import os
import zipfile
from pathlib import Path
import urllib.request
import ssl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAND_DIR = PROJECT_ROOT / "data" / "land"
LAND_DIR.mkdir(parents=True, exist_ok=True)

# SSL証明書検証を緩和（国土数値情報サーバー用）
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DOWNLOADS = {
    # 行政区域 (栃木県 = 09) - 2024年版
    "admin_boundary": {
        "url": "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_09_GML.zip",
        "desc": "行政区域 (栃木県)",
    },
    # 土地利用細分メッシュ (栃木県域を含む2次メッシュ)
    # 5539, 5540 が栃木県をカバー
    "land_use": {
        "url": "https://nlftp.mlit.go.jp/ksj/gml/data/L03-b-r/L03-b-r-21_5540-jgd_GeoTIFF.zip",
        "desc": "土地利用メッシュ (5540)",
    },
    # 森林地域
    "forest": {
        "url": "https://nlftp.mlit.go.jp/ksj/gml/data/A13/A13-21/A13-21_09_GML.zip",
        "desc": "森林地域 (栃木県)",
    },
    # 自然公園地域
    "nature_park": {
        "url": "https://nlftp.mlit.go.jp/ksj/gml/data/A10/A10-22/A10-22_09_GML.zip",
        "desc": "自然公園地域 (栃木県)",
    },
    # 農業地域
    "agriculture": {
        "url": "https://nlftp.mlit.go.jp/ksj/gml/data/A12/A12-15/A12-15_09_GML.zip",
        "desc": "農業地域 (栃木県)",
    },
}


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


def main():
    print("=" * 60)
    print("栃木県 土地利用・規制データ ダウンロード")
    print("=" * 60)

    for key, info in DOWNLOADS.items():
        dest = LAND_DIR / f"{key}.zip"
        extract_dir = LAND_DIR / key

        success = download_file(info["url"], dest, info["desc"])
        if success and dest.exists():
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                extract_zip(dest, extract_dir)
            except Exception as e:
                print(f"  Extract error: {e}")

    print("\n完了!")
    print(f"データ保存先: {LAND_DIR}")


if __name__ == "__main__":
    main()
