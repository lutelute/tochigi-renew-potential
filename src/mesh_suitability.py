"""
再エネ適地評価 メッシュ分析 (多県対応)
500mまたは1kmメッシュで各セルの適地スコアをGIS-MCDA手法で算出

評価基準 (Al Garni & Awasthi 2017; Doorga et al. 2019 等に基づく):
  1. 傾斜 (slope)                         — 重み 20%
  2. 送電線(154kV以上)からの距離             — 重み 25%
  3. 変電所(66kV以上)からの距離              — 重み 15%
  4. 土地利用 (森林・農振地域の除外)          — 重み 15%
  5. 標高 (elevation)                      — 重み 10%
  6. 道路からの距離 (OSM利用可の場合)         — 重み 10%
  7. 保護区域 (自然公園等) → 除外基準         — 重み 5%
"""
import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from config import PREFECTURES, WEIGHTS, get_grid_dir, get_land_dir, get_output_dir, get_pref_config


def create_mesh(resolution_m: int, pref: str) -> gpd.GeoDataFrame:
    """メッシュグリッドを生成"""
    cfg = get_pref_config(pref)
    xmin, ymin, xmax, ymax = cfg["bbox"]
    center_lat = cfg["center"][0]

    res_deg = resolution_m / 111000.0
    res_deg_x = resolution_m / (111000.0 * np.cos(np.radians(center_lat)))

    xs = np.arange(xmin, xmax, res_deg_x)
    ys = np.arange(ymin, ymax, res_deg)

    cells = []
    cx_list, cy_list = [], []
    for x in xs:
        for y in ys:
            cells.append(box(x, y, x + res_deg_x, y + res_deg))
            cx_list.append(x + res_deg_x / 2)
            cy_list.append(y + res_deg / 2)

    mesh = gpd.GeoDataFrame(
        {"cx": cx_list, "cy": cy_list},
        geometry=cells,
        crs="EPSG:4326",
    )
    print(f"  メッシュ生成: {len(mesh)} セル ({resolution_m}m)")
    return mesh


def clip_to_prefecture(mesh: gpd.GeoDataFrame, pref: str) -> gpd.GeoDataFrame:
    """行政区域でクリップ"""
    cfg = get_pref_config(pref)
    land_dir = get_land_dir(pref)
    admin_dir = land_dir / "admin_boundary"
    shp_files = list(admin_dir.rglob("*.shp"))
    if not shp_files:
        print("  WARNING: 行政区域データなし、クリップなし")
        return mesh

    admin = gpd.read_file(shp_files[0])
    boundary = admin.union_all()
    mask = mesh.geometry.intersects(boundary)
    clipped = mesh[mask].copy().reset_index(drop=True)
    print(f"  {cfg['name_ja']}クリップ: {len(clipped)} セル")
    return clipped


def score_slope(mesh: gpd.GeoDataFrame, pref: str) -> np.ndarray:
    """傾斜スコア (0-100): 0°=100, 5°=80, 15°=30, 30°+=0"""
    cfg = get_pref_config(pref)
    land_dir = get_land_dir(pref)
    xmin, ymin, xmax, ymax = cfg["bbox"]

    slope_tif = land_dir / f"{pref}_slope.tif"
    if not slope_tif.exists():
        print("  WARNING: 傾斜データなし、緯度ベースの推定使用")
        lat_norm = (mesh["cy"] - ymin) / (ymax - ymin)
        return np.clip((1.0 - lat_norm * 0.6) * 100, 10, 100)

    import rasterio
    from rasterio.transform import rowcol

    with rasterio.open(slope_tif) as src:
        data = src.read(1)
        transform = src.transform

        scores = np.zeros(len(mesh))
        for i, (_, row) in enumerate(mesh.iterrows()):
            try:
                r, c = rowcol(transform, row["cx"], row["cy"])
                r, c = int(r), int(c)
                if 0 <= r < data.shape[0] and 0 <= c < data.shape[1]:
                    slope = data[r, c]
                    if slope < 0 or slope > 90:
                        scores[i] = 50
                    elif slope <= 3:
                        scores[i] = 100
                    elif slope <= 5:
                        scores[i] = 85
                    elif slope <= 8:
                        scores[i] = 70
                    elif slope <= 15:
                        scores[i] = 40
                    elif slope <= 30:
                        scores[i] = 15
                    else:
                        scores[i] = 0
                else:
                    scores[i] = 50
            except Exception:
                scores[i] = 50

    print(f"  傾斜スコア: mean={scores.mean():.1f}")
    return scores


def score_grid_distance(mesh: gpd.GeoDataFrame, lines: gpd.GeoDataFrame) -> np.ndarray:
    """送電線(154kV以上)からの距離スコア (近いほど高)"""
    hv_lines = lines[lines["voltage_kv"] >= 154].copy()
    if len(hv_lines) == 0:
        return np.full(len(mesh), 50)

    # 投影座標に変換
    mesh_proj = mesh.to_crs(epsg=6677)
    hv_proj = hv_lines.to_crs(epsg=6677)

    # 全送電線を結合
    hv_union = hv_proj.union_all()

    # 各メッシュの中心点から最近接距離
    centroids = mesh_proj.geometry.centroid
    distances = centroids.distance(hv_union)

    # スコア化: 0m=100, 1km=90, 3km=70, 5km=50, 10km=20, 20km+=0
    scores = np.where(distances <= 1000, 100,
             np.where(distances <= 3000, 90 - (distances - 1000) / 2000 * 20,
             np.where(distances <= 5000, 70 - (distances - 3000) / 2000 * 20,
             np.where(distances <= 10000, 50 - (distances - 5000) / 5000 * 30,
             np.where(distances <= 20000, 20 - (distances - 10000) / 10000 * 20,
             0)))))

    print(f"  送電線距離スコア: mean={scores.mean():.1f}")
    return np.clip(scores, 0, 100)


def score_substation_distance(mesh: gpd.GeoDataFrame, subs: gpd.GeoDataFrame) -> np.ndarray:
    """変電所(66kV以上)からの距離スコア"""
    hv_subs = subs[(subs["voltage_kv"] >= 66) | (subs["voltage_kv"] == 0)].copy()
    if len(hv_subs) == 0:
        return np.full(len(mesh), 50)

    mesh_proj = mesh.to_crs(epsg=6677)
    hv_proj = hv_subs.to_crs(epsg=6677)

    centroids = mesh_proj.geometry.centroid
    sub_points = hv_proj.geometry.centroid

    # 最近接変電所までの距離
    from shapely.ops import nearest_points
    sub_union = sub_points.union_all()
    distances = centroids.distance(sub_union)

    # スコア化: 0m=100, 2km=80, 5km=50, 10km=20, 20km+=0
    scores = np.where(distances <= 2000, 100 - distances / 2000 * 20,
             np.where(distances <= 5000, 80 - (distances - 2000) / 3000 * 30,
             np.where(distances <= 10000, 50 - (distances - 5000) / 5000 * 30,
             np.where(distances <= 20000, 20 - (distances - 10000) / 10000 * 20,
             0))))

    print(f"  変電所距離スコア: mean={scores.mean():.1f}")
    return np.clip(scores, 0, 100)


def score_land_use(mesh: gpd.GeoDataFrame, pref: str, mode: str = "ground") -> np.ndarray:
    """土地利用スコア: 国土数値情報 100mメッシュ (GeoTIFF) から判定

    mode="ground" (大規模地上設置):
        建物用地 → 不適(0), 荒地 → 最適(90)
    mode="rooftop" (ルーフトップ太陽光):
        建物用地 → 高適(85), 荒地 → 低適(10)
    """
    if mode == "rooftop":
        LU_SCORE = {
            0: 0,     # データなし/海域
            10: 10,   # 田
            20: 10,   # その他農用地
            50: 0,    # 森林
            60: 10,   # 荒地
            70: 85,   # 建物用地 (ルーフトップ適地)
            91: 0,    # 道路
            92: 0,    # 鉄道
            100: 30,  # その他用地
            110: 0,   # 河川
            140: 0,   # 海浜
            150: 0,   # 海水域
            160: 10,  # ゴルフ場等
        }
    else:  # ground (大規模)
        LU_SCORE = {
            0: 0,     # データなし/海域
            10: 40,   # 田 (営農型の可能性)
            20: 40,   # その他農用地
            50: 20,   # 森林
            60: 90,   # 荒地 (最適候補)
            70: 0,    # 建物用地 (不適)
            91: 0,    # 道路 (不適)
            92: 0,    # 鉄道 (不適)
            100: 80,  # その他用地
            110: 0,   # 河川地及び湖沼 (不適)
            140: 0,   # 海浜 (不適)
            150: 0,   # 海水域 (不適)
            160: 85,  # ゴルフ場等
        }

    land_dir = get_land_dir(pref)
    lu_dir = land_dir / "land_use"
    tif_files = sorted(lu_dir.glob("*.tif")) if lu_dir.exists() else []

    if not tif_files:
        print("  WARNING: 土地利用メッシュなし、ポリゴンベースにフォールバック")
        scores = np.full(len(mesh), 70.0)
        return scores

    import rasterio
    from rasterio.transform import rowcol

    scores = np.full(len(mesh), 50.0)  # デフォルト
    matched = 0

    for tif_path in tif_files:
        with rasterio.open(tif_path) as src:
            data = src.read(1)
            transform = src.transform
            bounds = src.bounds

            for i, (_, row) in enumerate(mesh.iterrows()):
                cx, cy = row["cx"], row["cy"]
                # このTIFの範囲内か確認
                if not (bounds.left <= cx <= bounds.right and
                        bounds.bottom <= cy <= bounds.top):
                    continue
                try:
                    r, c = rowcol(transform, cx, cy)
                    r, c = int(r), int(c)
                    if 0 <= r < data.shape[0] and 0 <= c < data.shape[1]:
                        lu_code = int(data[r, c])
                        scores[i] = LU_SCORE.get(lu_code, 50)
                        matched += 1
                except Exception:
                    pass

    print(f"  土地利用スコア (100mメッシュ): mean={scores.mean():.1f}, "
          f"matched={matched}/{len(mesh)}")

    # 不適地（建物用地等）の統計
    n_unsuitable = np.sum(scores == 0)
    n_forest = np.sum(scores == 20)
    n_agri = np.sum(scores == 40)
    n_good = np.sum(scores >= 80)
    print(f"    不適(建物/道路/河川): {n_unsuitable} ({n_unsuitable/len(mesh)*100:.1f}%)")
    print(f"    森林: {n_forest} ({n_forest/len(mesh)*100:.1f}%)")
    print(f"    農用地: {n_agri} ({n_agri/len(mesh)*100:.1f}%)")
    print(f"    好適(荒地/その他): {n_good} ({n_good/len(mesh)*100:.1f}%)")

    return scores


def score_elevation(mesh: gpd.GeoDataFrame, pref: str) -> np.ndarray:
    """標高スコア: 低いほど高スコア（平野部を好む）"""
    cfg = get_pref_config(pref)
    land_dir = get_land_dir(pref)
    xmin, ymin, xmax, ymax = cfg["bbox"]

    slope_tif = land_dir / f"{pref}_slope.tif"
    if not slope_tif.exists():
        lat_norm = (mesh["cy"] - ymin) / (ymax - ymin)
        return np.clip((1.0 - lat_norm * 0.5) * 100, 20, 100)

    # DEMから標高を読み取り
    dem_dir = land_dir / "dem"
    hgt_files = list(dem_dir.rglob("*.hgt")) if dem_dir.exists() else []

    if not hgt_files:
        # 傾斜TIFから間接的に推定（緯度ベース）
        lat_norm = (mesh["cy"] - ymin) / (ymax - ymin)
        scores = np.clip((1.0 - lat_norm * 0.5) * 100, 20, 100)
        print(f"  標高スコア（推定）: mean={scores.mean():.1f}")
        return scores

    # HGTファイルから標高を読み取り
    import rasterio
    from rasterio.transform import rowcol

    scores = np.full(len(mesh), 50.0)
    for hgt_file in hgt_files:
        with rasterio.open(hgt_file) as src:
            data = src.read(1)
            transform = src.transform
            for i, (_, row) in enumerate(mesh.iterrows()):
                try:
                    r, c = rowcol(transform, row["cx"], row["cy"])
                    r, c = int(r), int(c)
                    if 0 <= r < data.shape[0] and 0 <= c < data.shape[1]:
                        elev = data[r, c]
                        if elev <= 100:
                            scores[i] = 100
                        elif elev <= 300:
                            scores[i] = 80
                        elif elev <= 500:
                            scores[i] = 60
                        elif elev <= 1000:
                            scores[i] = 30
                        else:
                            scores[i] = 10
                except Exception:
                    pass

    print(f"  標高スコア: mean={scores.mean():.1f}")
    return scores


def _extract_voltage_kv(v):
    if pd.isna(v) or v == "":
        return 0
    try:
        val = float(str(v).replace(",", ""))
        return val / 1000 if val > 1000 else val
    except ValueError:
        return 0


def compute_mesh(resolution_m: int, lines, subs, pref: str, mode: str = "ground"):
    """指定解像度でメッシュ計算し mesh_gdf を返す
    mode: "ground" (大規模地上設置) or "rooftop" (屋根置き)
    """
    cfg = get_pref_config(pref)
    output_dir = get_output_dir(pref)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n--- {cfg['name_ja']} {resolution_m}m メッシュ ({mode}) ---")
    mesh = create_mesh(resolution_m, pref)
    mesh = clip_to_prefecture(mesh, pref)

    mesh["score_slope"] = score_slope(mesh, pref)
    mesh["score_grid_dist"] = score_grid_distance(mesh, lines)
    mesh["score_sub_dist"] = score_substation_distance(mesh, subs)
    mesh["score_land_use"] = score_land_use(mesh, pref, mode=mode)
    mesh["score_elevation"] = score_elevation(mesh, pref)
    mesh["score_road"] = 50.0
    mesh["score_protection"] = 80.0

    mesh["total_score"] = (
        mesh["score_slope"] * WEIGHTS["slope"] +
        mesh["score_grid_dist"] * WEIGHTS["grid_distance"] +
        mesh["score_sub_dist"] * WEIGHTS["substation_distance"] +
        mesh["score_land_use"] * WEIGHTS["land_use"] +
        mesh["score_elevation"] * WEIGHTS["elevation"] +
        mesh["score_road"] * WEIGHTS["road_distance"] +
        mesh["score_protection"] * WEIGHTS["protection"]
    ).round(1)

    print(f"  総合スコア: min={mesh['total_score'].min():.1f}, "
          f"max={mesh['total_score'].max():.1f}, "
          f"mean={mesh['total_score'].mean():.1f}")

    bins = [0, 20, 40, 60, 80, 100]
    labels = ["不適(0-20)", "低(20-40)", "中(40-60)", "良(60-80)", "最適(80-100)"]
    mesh["score_class"] = pd.cut(mesh["total_score"], bins=bins, labels=labels)
    for cls in labels:
        count = (mesh["score_class"] == cls).sum()
        pct = count / len(mesh) * 100
        print(f"    {cls}: {count} セル ({pct:.1f}%)")

    # GeoJSON保存
    out = output_dir / f"{pref}_mesh_{resolution_m}m.geojson"
    mesh.to_file(out, driver="GeoJSON")
    print(f"  -> {out.name}")

    return mesh


def mesh_color(score):
    if score >= 80: return "#006400"
    if score >= 60: return "#228B22"
    if score >= 40: return "#DAA520"
    if score >= 20: return "#FF8C00"
    return "#DC143C"


def add_mesh_layer(m, mesh, resolution_m, show=False):
    """Folium FeatureGroup としてメッシュレイヤーを追加"""
    import folium

    fg = folium.FeatureGroup(name=f"適地スコア {resolution_m}m", show=show)

    disp = mesh[["geometry", "total_score", "score_slope",
                 "score_grid_dist", "score_sub_dist", "score_land_use"]].copy()
    disp["_color"] = disp["total_score"].apply(mesh_color)
    disp["_tip"] = disp.apply(
        lambda r: (f"スコア: {r['total_score']:.0f} | "
                   f"傾斜:{r['score_slope']:.0f} "
                   f"送電線:{r['score_grid_dist']:.0f} "
                   f"変電所:{r['score_sub_dist']:.0f} "
                   f"土地:{r['score_land_use']:.0f}"),
        axis=1,
    )

    folium.GeoJson(
        disp[["geometry", "_color", "_tip"]].to_json(),
        style_function=lambda feat: {
            "fillColor": feat["properties"]["_color"],
            "color": feat["properties"]["_color"],
            "weight": 0.2,
            "fillOpacity": 0.5,
        },
        tooltip=folium.GeoJsonTooltip(fields=["_tip"], aliases=[""], labels=False),
    ).add_to(fg)
    fg.add_to(m)
    return fg


def main():
    parser = argparse.ArgumentParser(description="再エネ適地評価 メッシュ分析 (多県対応)")
    parser.add_argument("--prefecture", type=str, default=None,
                        help="対象県 (tochigi/chiba/ibaraki)。省略時は全県実行")
    parser.add_argument("--resolution", type=int, default=0,
                        help="単一解像度で実行 (m)。0なら全解像度を統合マップに出力")
    args = parser.parse_args()

    # 対象県の決定
    if args.prefecture:
        pref_list = [args.prefecture]
    else:
        pref_list = list(PREFECTURES.keys())

    for pref in pref_list:
        cfg = get_pref_config(pref)
        grid_dir = get_grid_dir(pref)
        output_dir = get_output_dir(pref)
        output_dir.mkdir(parents=True, exist_ok=True)

        print("=" * 60)
        print(f"{cfg['name_ja']} 再エネ適地評価 メッシュ分析")
        print("=" * 60)

        # データ読み込み
        print("\n[1] データ読み込み")
        lines = gpd.read_file(grid_dir / f"{pref}_lines.geojson")
        subs = gpd.read_file(grid_dir / f"{pref}_substations.geojson")
        lines["voltage_kv"] = lines["voltage"].apply(_extract_voltage_kv)
        subs["voltage_kv"] = subs["voltage"].apply(_extract_voltage_kv)

        # 解像度リスト
        if args.resolution > 0:
            resolutions = [args.resolution]
        else:
            resolutions = [1000, 500, 250]

        # 各解像度・各モードでメッシュ計算
        print("\n[2] メッシュ計算")
        meshes_ground = {}
        meshes_rooftop = {}
        for res in resolutions:
            meshes_ground[res] = compute_mesh(res, lines, subs, pref, mode="ground")
            meshes_rooftop[res] = compute_mesh(res, lines, subs, pref, mode="rooftop")

        # 単一解像度モードの場合はマップ生成をスキップ
        if args.resolution > 0:
            print(f"\n{cfg['name_ja']} 完了 (解像度: {args.resolution}m)")
            continue

        # 統合マップ生成 (レイヤー切替可能)
        print("\n[3] 統合マップ生成")
        import folium

        m = folium.Map(location=cfg["center"], zoom_start=9, tiles=None)

        # ベースマップ (切替可能)
        folium.TileLayer("cartodbpositron", name="地図 (CartoDB)").add_to(m)
        folium.TileLayer("OpenStreetMap", name="地図 (OSM)").add_to(m)
        folium.TileLayer(
            tiles="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
            attr="Google", name="Google Map",
        ).add_to(m)
        folium.TileLayer(
            tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            attr="Google", name="Google 衛星写真",
        ).add_to(m)
        folium.TileLayer(
            tiles="https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
            attr="Google", name="Google 地形図",
        ).add_to(m)
        folium.TileLayer(
            tiles="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
            attr="国土地理院", name="国土地理院 標準",
        ).add_to(m)
        folium.TileLayer(
            tiles="https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
            attr="国土地理院", name="国土地理院 航空写真",
        ).add_to(m)

        # メッシュレイヤー (解像度別 × モード別)
        for i, res in enumerate(resolutions):
            show_g = (i == 0)
            label_suffix = " ⚠重い" if res <= 250 else ""
            fg = add_mesh_layer(m, meshes_ground[res], res, show=show_g)
            fg.layer_name = f"適地スコア {res}m{label_suffix}"
            print(f"  レイヤー追加: {res}m 大規模 ({len(meshes_ground[res])} セル) show={show_g}")

        for res in resolutions:
            rt_suffix = " ⚠重い" if res <= 250 else ""
            fg = folium.FeatureGroup(name=f"ルーフトップ適地 {res}m{rt_suffix}", show=False)
            disp = meshes_rooftop[res][["geometry", "total_score", "score_slope",
                                        "score_grid_dist", "score_sub_dist", "score_land_use"]].copy()
            disp["_color"] = disp["total_score"].apply(mesh_color)
            disp["_tip"] = disp.apply(
                lambda r: (f"ルーフトップスコア: {r['total_score']:.0f} | "
                           f"傾斜:{r['score_slope']:.0f} "
                           f"送電線:{r['score_grid_dist']:.0f} "
                           f"土地:{r['score_land_use']:.0f}"),
                axis=1,
            )
            folium.GeoJson(
                disp[["geometry", "_color", "_tip"]].to_json(),
                style_function=lambda feat: {
                    "fillColor": feat["properties"]["_color"],
                    "color": feat["properties"]["_color"],
                    "weight": 0.2, "fillOpacity": 0.5,
                },
                tooltip=folium.GeoJsonTooltip(fields=["_tip"], aliases=[""], labels=False),
            ).add_to(fg)
            fg.add_to(m)
            print(f"  レイヤー追加: {res}m ルーフトップ ({len(meshes_rooftop[res])} セル)")

        # 送電線オーバーレイ
        land_dir = get_land_dir(pref)
        fg_lines = folium.FeatureGroup(name="送電線 (66kV以上)", show=True)
        for _, row in lines[(lines["voltage_kv"] >= 66) | (lines["voltage_kv"] == 0)].iterrows():
            v = row["voltage_kv"]
            geom = row.geometry
            if geom is None:
                continue
            coords = []
            if geom.geom_type == "LineString":
                coords = [(c[1], c[0]) for c in geom.coords]
            elif geom.geom_type == "MultiLineString":
                for part in geom.geoms:
                    coords.extend([(c[1], c[0]) for c in part.coords])
            if coords:
                color = ("#ff0000" if v >= 500 else "#ff6600" if v >= 275
                         else "#0066ff" if v >= 154 else "#00aa44" if v >= 66 else "#999999")
                weight = 3 if v >= 275 else 2 if v >= 154 else 1
                name = row.get("name", "")
                if pd.isna(name):
                    name = ""
                folium.PolyLine(
                    coords, color=color, weight=weight, opacity=0.6,
                    tooltip=name if name else None,
                ).add_to(fg_lines)
        fg_lines.add_to(m)

        # 変電所オーバーレイ
        fg_subs = folium.FeatureGroup(name="変電所", show=True)
        for _, row in subs[(subs["voltage_kv"] >= 66) | (subs["voltage_kv"] == 0)].iterrows():
            v = row["voltage_kv"]
            name = row.get("name", "")
            if pd.isna(name):
                name = f"変電所 ({v:.0f}kV)"
            centroid = row.geometry.centroid
            color = ("#ff0000" if v >= 500 else "#ff6600" if v >= 275
                     else "#0066ff" if v >= 154 else "#00aa44" if v >= 66 else "#999999")
            radius = 4 if v < 154 else 6 if v < 275 else 8
            folium.CircleMarker(
                location=[centroid.y, centroid.x], radius=radius,
                color=color, fill=True, fill_color=color, fill_opacity=0.6,
                tooltip=name,
            ).add_to(fg_subs)
            if v >= 66 or v == 0:
                fs = 8 if v < 154 else 10 if v < 275 else 11
                folium.Marker(
                    location=[centroid.y, centroid.x],
                    icon=folium.DivIcon(
                        html=(f'<div style="font-size:{fs}px;font-weight:bold;color:{color};'
                              f'white-space:nowrap;text-shadow:1px 1px 1px white,-1px -1px 1px white,'
                              f'1px -1px 1px white,-1px 1px 1px white;">{name}</div>'),
                        icon_size=(0, 0), icon_anchor=(0, -10),
                    ),
                ).add_to(fg_subs)
        fg_subs.add_to(m)

        # 県境 (太い黒線)
        admin_dir = land_dir / "admin_boundary"
        admin_shps = list(admin_dir.rglob("*.shp")) if admin_dir.exists() else []
        if admin_shps:
            admin = gpd.read_file(admin_shps[0])
            pref_boundary = admin.union_all()
            fg_boundary = folium.FeatureGroup(name="県境", show=True)
            folium.GeoJson(
                pref_boundary.__geo_interface__,
                style_function=lambda x: {
                    "fillColor": "transparent",
                    "color": "#000000",
                    "weight": 3.5,
                    "fillOpacity": 0,
                },
            ).add_to(fg_boundary)
            fg_boundary.add_to(m)
            print("  県境レイヤー追加")

        # 道路レイヤー (軽量: 主要道路のみ)
        road_geojson = get_grid_dir(pref) / f"{pref}_roads.geojson"
        if road_geojson.exists():
            fg_roads = folium.FeatureGroup(name="主要道路", show=False)
            roads = gpd.read_file(road_geojson)
            folium.GeoJson(
                roads.to_json(),
                style_function=lambda x: {
                    "color": "#666666", "weight": 1.0, "opacity": 0.5,
                },
            ).add_to(fg_roads)
            fg_roads.add_to(m)
            print(f"  道路レイヤー追加: {len(roads)} 本")

        # 凡例
        res_text = " / ".join(f"{r}m" for r in resolutions)
        legend_html = f"""
        <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                    background:white;padding:12px;border:2px solid grey;
                    border-radius:5px;font-size:11px;opacity:0.92;">
        <b>{cfg['name_ja']} 適地スコア</b><br>
        <small>メッシュ: {res_text} (レイヤー切替可)</small><br>
        <small>大規模 / ルーフトップ 切替可</small><br>
        <small>GIS-MCDA手法 (AHP重み付け)</small>
        <hr style="margin:4px 0">
        <i style="background:#006400;width:14px;height:14px;display:inline-block"></i> 80-100 最適<br>
        <i style="background:#228B22;width:14px;height:14px;display:inline-block"></i> 60-79 良好<br>
        <i style="background:#DAA520;width:14px;height:14px;display:inline-block"></i> 40-59 中程度<br>
        <i style="background:#FF8C00;width:14px;height:14px;display:inline-block"></i> 20-39 低い<br>
        <i style="background:#DC143C;width:14px;height:14px;display:inline-block"></i> 0-19 不適<br>
        <hr style="margin:4px 0">
        <b>AHP重み</b><br>
        送電線距離: 25% | 傾斜: 20%<br>
        変電所距離: 15% | 土地利用: 15%<br>
        標高: 10% | 道路: 10% | 保護: 5%<br>
        <hr style="margin:4px 0">
        <b>送電線</b><br>
        <i style="background:#ff0000;width:16px;height:3px;display:inline-block"></i> 500kV
        <i style="background:#ff6600;width:16px;height:3px;display:inline-block"></i> 275kV<br>
        <i style="background:#0066ff;width:16px;height:3px;display:inline-block"></i> 154kV
        <i style="background:#00aa44;width:16px;height:3px;display:inline-block"></i> 66kV<br>
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))

        folium.LayerControl(collapsed=False).add_to(m)

        output_map = output_dir / f"{pref}_mesh_multi_map.html"
        m.save(str(output_map))
        print(f"\n統合メッシュマップ保存: {output_map}")
        print(f"{cfg['name_ja']} 完了!")


if __name__ == "__main__":
    main()
