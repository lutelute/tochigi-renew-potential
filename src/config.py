"""
関東圏 再エネポテンシャル評価 — 県別設定
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PREFECTURES = {
    "tochigi": {
        "name_ja": "栃木県",
        "code": "09",
        "bbox": (139.32, 36.19, 140.30, 37.16),
        "center": [36.65, 139.88],
        "srtm_tiles": ["N36E139", "N36E140", "N37E139", "N37E140"],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-01-07",
    },
    "chiba": {
        "name_ja": "千葉県",
        "code": "12",
        "bbox": (139.74, 34.87, 140.87, 36.11),
        "center": [35.60, 140.12],
        "srtm_tiles": ["N34E139", "N34E140", "N35E139", "N35E140"],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-07-30",
    },
    "ibaraki": {
        "name_ja": "茨城県",
        "code": "08",
        "bbox": (139.68, 35.73, 140.85, 36.97),
        "center": [36.34, 140.45],
        "srtm_tiles": ["N35E139", "N35E140", "N36E139", "N36E140"],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-08-06",
    },
}

# AHP重み (全県共通)
WEIGHTS = {
    "slope": 0.20,
    "grid_distance": 0.25,
    "substation_distance": 0.15,
    "land_use": 0.15,
    "elevation": 0.10,
    "road_distance": 0.10,
    "protection": 0.05,
}


def get_data_dir(pref: str) -> Path:
    return PROJECT_ROOT / "data" / pref


def get_grid_dir(pref: str) -> Path:
    return get_data_dir(pref) / "grid"


def get_land_dir(pref: str) -> Path:
    return get_data_dir(pref) / "land"


def get_potential_dir(pref: str) -> Path:
    return get_data_dir(pref) / "potential"


def get_output_dir(pref: str) -> Path:
    return PROJECT_ROOT / "output" / pref


def get_docs_dir(pref: str) -> Path:
    return PROJECT_ROOT / "docs" / pref


def get_pref_config(pref: str) -> dict:
    if pref not in PREFECTURES:
        raise ValueError(f"Unknown prefecture: {pref}. Choose from {list(PREFECTURES.keys())}")
    return PREFECTURES[pref]
