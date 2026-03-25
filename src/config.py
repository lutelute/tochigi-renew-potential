"""
全国47都道府県 再エネポテンシャル評価 — 県別設定
"""
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def compute_srtm_tiles(bbox: tuple) -> list[str]:
    """bboxからSRTMタイル名を自動計算する。
    bbox: (west, south, east, north) in degrees
    Returns: list of tile names like ["N35E139", "N36E139", ...]
    """
    west, south, east, north = bbox
    lat_min = int(math.floor(south))
    lat_max = int(math.floor(north))
    lon_min = int(math.floor(west))
    lon_max = int(math.floor(east))

    tiles = []
    for lat in range(lat_min, lat_max + 1):
        for lon in range(lon_min, lon_max + 1):
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            tiles.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
    return sorted(tiles)


# 電力エリア名 (All-Japan-Grid のファイル名に対応)
# hokkaido, tohoku, tokyo, chubu, hokuriku, kansai, chugoku, shikoku, kyushu, okinawa

PREFECTURES = {
    # ============================================================
    # 北海道 — 14振興局に分割 (5m計算のメモリ制約のため)
    # ============================================================
    "hokkaido_ishikari": {
        "name_ja": "北海道（石狩）",
        "code": "01",
        "bbox": (140.85, 42.85, 141.75, 43.55),
        "center": [43.20, 141.35],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_sorachi": {
        "name_ja": "北海道（空知）",
        "code": "01",
        "bbox": (141.50, 43.00, 142.65, 43.90),
        "center": [43.45, 142.05],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_shiribeshi": {
        "name_ja": "北海道（後志）",
        "code": "01",
        "bbox": (139.80, 42.60, 141.00, 43.40),
        "center": [43.00, 140.40],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_iburi": {
        "name_ja": "北海道（胆振）",
        "code": "01",
        "bbox": (140.50, 42.25, 141.60, 42.90),
        "center": [42.55, 141.05],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_hidaka": {
        "name_ja": "北海道（日高）",
        "code": "01",
        "bbox": (142.00, 42.00, 143.30, 42.90),
        "center": [42.45, 142.65],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_oshima": {
        "name_ja": "北海道（渡島）",
        "code": "01",
        "bbox": (139.80, 41.35, 141.20, 42.20),
        "center": [41.80, 140.50],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_hiyama": {
        "name_ja": "北海道（檜山）",
        "code": "01",
        "bbox": (139.50, 41.60, 140.30, 42.60),
        "center": [42.10, 139.90],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_kamikawa": {
        "name_ja": "北海道（上川）",
        "code": "01",
        "bbox": (142.00, 43.00, 143.50, 44.30),
        "center": [43.65, 142.75],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_rumoi": {
        "name_ja": "北海道（留萌）",
        "code": "01",
        "bbox": (141.40, 43.60, 142.20, 44.50),
        "center": [44.05, 141.80],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_soya": {
        "name_ja": "北海道（宗谷）",
        "code": "01",
        "bbox": (141.60, 44.50, 142.60, 45.60),
        "center": [45.05, 142.10],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_okhotsk": {
        "name_ja": "北海道（オホーツク）",
        "code": "01",
        "bbox": (142.50, 43.50, 145.00, 44.95),
        "center": [44.20, 143.75],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_tokachi": {
        "name_ja": "北海道（十勝）",
        "code": "01",
        "bbox": (142.30, 42.30, 143.80, 43.50),
        "center": [42.90, 143.05],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_kushiro": {
        "name_ja": "北海道（釧路）",
        "code": "01",
        "bbox": (143.50, 42.85, 145.10, 43.50),
        "center": [43.15, 144.30],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    "hokkaido_nemuro": {
        "name_ja": "北海道（根室）",
        "code": "01",
        "bbox": (144.70, 43.00, 145.90, 43.60),
        "center": [43.30, 145.30],
        "epsg_jpc": 6680,
        "grid_area": "hokkaido",
    },
    # ============================================================
    # 東北
    # ============================================================
    "aomori": {
        "name_ja": "青森県",
        "code": "02",
        "bbox": (139.49, 40.21, 141.68, 41.56),
        "center": [40.82, 140.74],
        "epsg_jpc": 6678,
        "grid_area": "tohoku",
    },
    "iwate": {
        "name_ja": "岩手県",
        "code": "03",
        "bbox": (139.64, 38.74, 142.07, 40.45),
        "center": [39.70, 141.15],
        "epsg_jpc": 6678,
        "grid_area": "tohoku",
    },
    "miyagi": {
        "name_ja": "宮城県",
        "code": "04",
        "bbox": (140.27, 37.77, 141.68, 39.00),
        "center": [38.27, 140.87],
        "epsg_jpc": 6678,
        "grid_area": "tohoku",
    },
    "akita": {
        "name_ja": "秋田県",
        "code": "05",
        "bbox": (139.69, 38.87, 140.99, 40.52),
        "center": [39.72, 140.10],
        "epsg_jpc": 6678,
        "grid_area": "tohoku",
    },
    "yamagata": {
        "name_ja": "山形県",
        "code": "06",
        "bbox": (139.52, 37.73, 140.64, 39.21),
        "center": [38.24, 140.33],
        "epsg_jpc": 6678,
        "grid_area": "tohoku",
    },
    "fukushima": {
        "name_ja": "福島県",
        "code": "07",
        "bbox": (139.16, 36.79, 141.05, 37.97),
        "center": [37.75, 140.47],
        "epsg_jpc": 6677,
        "grid_area": "tohoku",
    },
    # ============================================================
    # 関東
    # ============================================================
    "ibaraki": {
        "name_ja": "茨城県",
        "code": "08",
        "bbox": (139.68, 35.73, 140.85, 36.97),
        "center": [36.34, 140.45],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-08-06",
    },
    "tochigi": {
        "name_ja": "栃木県",
        "code": "09",
        "bbox": (139.32, 36.19, 140.30, 37.16),
        "center": [36.65, 139.88],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-01-07",
    },
    "gunma": {
        "name_ja": "群馬県",
        "code": "10",
        "bbox": (138.63, 36.07, 139.68, 37.07),
        "center": [36.39, 139.06],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
    },
    "saitama": {
        "name_ja": "埼玉県",
        "code": "11",
        "bbox": (138.87, 35.77, 139.91, 36.29),
        "center": [35.86, 139.65],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
    },
    "chiba": {
        "name_ja": "千葉県",
        "code": "12",
        "bbox": (139.74, 34.87, 140.87, 36.11),
        "center": [35.60, 140.12],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
        "capacity_pdf_date": "2025-07-30",
    },
    "tokyo": {
        "name_ja": "東京都",
        "code": "13",
        "bbox": (138.94, 35.50, 139.92, 35.90),
        "center": [35.68, 139.69],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
    },
    "kanagawa": {
        "name_ja": "神奈川県",
        "code": "14",
        "bbox": (138.91, 35.13, 139.79, 35.67),
        "center": [35.45, 139.64],
        "epsg_jpc": 6677,
        "grid_area": "tokyo",
    },
    # ============================================================
    # 中部
    # ============================================================
    "niigata": {
        "name_ja": "新潟県",
        "code": "15",
        "bbox": (137.72, 36.75, 140.05, 38.55),
        "center": [37.90, 139.02],
        "epsg_jpc": 6676,
        "grid_area": "tohoku",
    },
    "toyama": {
        "name_ja": "富山県",
        "code": "16",
        "bbox": (136.77, 36.27, 137.76, 36.99),
        "center": [36.70, 137.21],
        "epsg_jpc": 6675,
        "grid_area": "hokuriku",
    },
    "ishikawa": {
        "name_ja": "石川県",
        "code": "17",
        "bbox": (136.23, 36.07, 137.40, 37.86),
        "center": [36.59, 136.63],
        "epsg_jpc": 6675,
        "grid_area": "hokuriku",
    },
    "fukui": {
        "name_ja": "福井県",
        "code": "18",
        "bbox": (135.55, 35.37, 136.82, 36.29),
        "center": [35.85, 136.22],
        "epsg_jpc": 6675,
        "grid_area": "hokuriku",
    },
    "yamanashi": {
        "name_ja": "山梨県",
        "code": "19",
        "bbox": (138.18, 35.19, 139.15, 35.97),
        "center": [35.66, 138.57],
        "epsg_jpc": 6676,
        "grid_area": "tokyo",
    },
    "nagano": {
        "name_ja": "長野県",
        "code": "20",
        "bbox": (137.32, 35.19, 138.73, 37.03),
        "center": [36.23, 138.18],
        "epsg_jpc": 6676,
        "grid_area": "chubu",
    },
    "gifu": {
        "name_ja": "岐阜県",
        "code": "21",
        "bbox": (136.26, 35.14, 137.65, 36.47),
        "center": [35.39, 136.72],
        "epsg_jpc": 6675,
        "grid_area": "chubu",
    },
    "shizuoka": {
        "name_ja": "静岡県",
        "code": "22",
        "bbox": (137.48, 34.58, 139.18, 35.64),
        "center": [34.98, 138.38],
        "epsg_jpc": 6676,
        "grid_area": "chubu",
    },
    "aichi": {
        "name_ja": "愛知県",
        "code": "23",
        "bbox": (136.67, 34.57, 137.84, 35.43),
        "center": [35.18, 136.91],
        "epsg_jpc": 6675,
        "grid_area": "chubu",
    },
    # ============================================================
    # 近畿
    # ============================================================
    "mie": {
        "name_ja": "三重県",
        "code": "24",
        "bbox": (135.85, 33.72, 136.99, 35.17),
        "center": [34.73, 136.51],
        "epsg_jpc": 6674,
        "grid_area": "chubu",
    },
    "shiga": {
        "name_ja": "滋賀県",
        "code": "25",
        "bbox": (135.76, 34.82, 136.45, 35.71),
        "center": [35.00, 135.87],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    "kyoto": {
        "name_ja": "京都府",
        "code": "26",
        "bbox": (134.85, 34.77, 136.06, 35.78),
        "center": [35.02, 135.76],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    "osaka": {
        "name_ja": "大阪府",
        "code": "27",
        "bbox": (135.09, 34.27, 135.74, 34.98),
        "center": [34.69, 135.52],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    "hyogo": {
        "name_ja": "兵庫県",
        "code": "28",
        "bbox": (134.26, 34.15, 135.47, 35.68),
        "center": [34.69, 135.18],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    "nara": {
        "name_ja": "奈良県",
        "code": "29",
        "bbox": (135.57, 33.85, 136.23, 34.79),
        "center": [34.69, 135.83],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    "wakayama": {
        "name_ja": "和歌山県",
        "code": "30",
        "bbox": (135.05, 33.42, 136.07, 34.38),
        "center": [33.95, 135.17],
        "epsg_jpc": 6674,
        "grid_area": "kansai",
    },
    # ============================================================
    # 中国
    # ============================================================
    "tottori": {
        "name_ja": "鳥取県",
        "code": "31",
        "bbox": (133.15, 35.07, 134.53, 35.62),
        "center": [35.50, 134.24],
        "epsg_jpc": 6673,
        "grid_area": "chugoku",
    },
    "shimane": {
        "name_ja": "島根県",
        "code": "32",
        "bbox": (131.66, 34.30, 133.39, 36.07),
        "center": [35.47, 132.77],
        "epsg_jpc": 6672,
        "grid_area": "chugoku",
    },
    "okayama": {
        "name_ja": "岡山県",
        "code": "33",
        "bbox": (133.26, 34.33, 134.42, 35.36),
        "center": [34.66, 133.93],
        "epsg_jpc": 6673,
        "grid_area": "chugoku",
    },
    "hiroshima": {
        "name_ja": "広島県",
        "code": "34",
        "bbox": (132.03, 34.04, 133.41, 35.12),
        "center": [34.40, 132.46],
        "epsg_jpc": 6672,
        "grid_area": "chugoku",
    },
    "yamaguchi": {
        "name_ja": "山口県",
        "code": "35",
        "bbox": (130.82, 33.73, 132.11, 34.77),
        "center": [34.19, 131.47],
        "epsg_jpc": 6672,
        "grid_area": "chugoku",
    },
    # ============================================================
    # 四国
    # ============================================================
    "tokushima": {
        "name_ja": "徳島県",
        "code": "36",
        "bbox": (133.55, 33.50, 134.82, 34.25),
        "center": [34.07, 134.56],
        "epsg_jpc": 6673,
        "grid_area": "shikoku",
    },
    "kagawa": {
        "name_ja": "香川県",
        "code": "37",
        "bbox": (133.48, 34.09, 134.46, 34.51),
        "center": [34.34, 134.04],
        "epsg_jpc": 6673,
        "grid_area": "shikoku",
    },
    "ehime": {
        "name_ja": "愛媛県",
        "code": "38",
        "bbox": (132.01, 32.90, 133.68, 34.01),
        "center": [33.84, 132.77],
        "epsg_jpc": 6673,
        "grid_area": "shikoku",
    },
    "kochi": {
        "name_ja": "高知県",
        "code": "39",
        "bbox": (132.47, 32.71, 134.31, 33.88),
        "center": [33.56, 133.53],
        "epsg_jpc": 6673,
        "grid_area": "shikoku",
    },
    # ============================================================
    # 九州
    # ============================================================
    "fukuoka": {
        "name_ja": "福岡県",
        "code": "40",
        "bbox": (130.02, 33.00, 131.19, 33.97),
        "center": [33.61, 130.42],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    "saga": {
        "name_ja": "佐賀県",
        "code": "41",
        "bbox": (129.74, 32.95, 130.56, 33.60),
        "center": [33.25, 130.30],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    "nagasaki": {
        "name_ja": "長崎県",
        "code": "42",
        "bbox": (128.60, 32.57, 130.35, 34.73),
        "center": [32.75, 129.87],
        "epsg_jpc": 6670,
        "grid_area": "kyushu",
    },
    "kumamoto": {
        "name_ja": "熊本県",
        "code": "43",
        "bbox": (130.10, 32.07, 131.33, 33.20),
        "center": [32.79, 130.74],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    "oita": {
        "name_ja": "大分県",
        "code": "44",
        "bbox": (130.83, 32.71, 132.12, 33.75),
        "center": [33.24, 131.61],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    "miyazaki": {
        "name_ja": "宮崎県",
        "code": "45",
        "bbox": (130.68, 31.35, 131.89, 32.84),
        "center": [31.91, 131.42],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    "kagoshima": {
        "name_ja": "鹿児島県",
        "code": "46",
        "bbox": (129.43, 30.20, 131.33, 32.26),
        "center": [31.56, 130.56],
        "epsg_jpc": 6671,
        "grid_area": "kyushu",
    },
    # ============================================================
    # 沖縄
    # ============================================================
    "okinawa": {
        "name_ja": "沖縄県",
        "code": "47",
        "bbox": (126.15, 24.04, 128.32, 26.90),
        "center": [26.33, 127.80],
        "epsg_jpc": 6691,
        "grid_area": "okinawa",
    },
}

# srtm_tiles は bbox から自動計算 (明示的に指定されていない場合)
for _key, _cfg in PREFECTURES.items():
    if "srtm_tiles" not in _cfg:
        _cfg["srtm_tiles"] = compute_srtm_tiles(_cfg["bbox"])

# AHP重み (全県共通)
WEIGHTS = {
    "slope": 0.20,
    "grid_distance": 0.15,
    "distribution_line_distance": 0.10,
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
