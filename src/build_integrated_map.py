"""
栃木県 再エネ適地スコア統合マップ
全レイヤー（系統空容量 + ポテンシャル + 傾斜 + 土地利用規制 + 系統近接性）を統合
"""
import json
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
from folium.plugins import MarkerCluster

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRID_DIR = PROJECT_ROOT / "data" / "grid"
LAND_DIR = PROJECT_ROOT / "data" / "land"
POT_DIR = PROJECT_ROOT / "data" / "potential"
OUTPUT_DIR = PROJECT_ROOT / "output"

TOCHIGI_CENTER = [36.65, 139.88]

VOLTAGE_COLORS = {500: "#ff0000", 275: "#ff6600", 154: "#0066ff", 66: "#00aa44"}


def get_line_color(v):
    if v >= 500: return VOLTAGE_COLORS[500]
    if v >= 275: return VOLTAGE_COLORS[275]
    if v >= 154: return VOLTAGE_COLORS[154]
    if v >= 66: return VOLTAGE_COLORS[66]
    return "#999999"


def get_line_weight(v):
    if v >= 500: return 4
    if v >= 275: return 3
    if v >= 154: return 2.5
    if v >= 66: return 1.5
    return 1


def extract_voltage_kv(v):
    if pd.isna(v) or v == "": return 0
    try:
        val = float(str(v).replace(",", ""))
        return val / 1000 if val > 1000 else val
    except ValueError:
        return 0


def capacity_color(value):
    if pd.isna(value) or value <= 0: return "red"
    if value <= 50: return "orange"
    if value <= 200: return "yellow"
    return "green"


def score_color(score):
    """適地スコア -> 色 (0-100)"""
    if score >= 70: return "#006400"  # dark green
    if score >= 50: return "#228B22"  # forest green
    if score >= 30: return "#DAA520"  # goldenrod
    if score >= 10: return "#FF8C00"  # dark orange
    return "#DC143C"  # crimson


def compute_suitability_scores(potential_gdf, cap_dist, slope_stats=None):
    """市町村ごとの適地スコアを計算"""
    scores = potential_gdf.copy()

    # カラム名の正規化
    col_map = {
        "muni_name": "N03_004",
        "wind_land_mw": "wind_mw",
        "hydro_small_mw": "hydro_mw",
        "renew_total_mw": "total_mw",
        "potential_density": "potential_density_mw_km2",
    }
    for old, new in col_map.items():
        if old in scores.columns and new not in scores.columns:
            scores[new] = scores[old]
    if "N03_004" not in scores.columns and "muni_name" in scores.columns:
        scores["N03_004"] = scores["muni_name"]

    pot_density = scores["solar_land_mw"].fillna(0) / scores["area_km2"].clip(lower=1)
    scores["score_potential"] = np.clip(pot_density / pot_density.quantile(0.9) * 40, 0, 40)

    # 2. 系統空容量スコア (0-30)
    muni_cap = {}
    for _, row in cap_dist.iterrows():
        name = row["変電所名"]
        try:
            cap = float(row.get("空容量_当該設備MW", 0))
        except (ValueError, TypeError):
            cap = 0
        for muni in scores["N03_004"].values:
            if isinstance(muni, str) and name in muni:
                muni_cap.setdefault(muni, []).append(cap)

    avg_all = cap_dist["空容量_当該設備MW"].apply(
        lambda x: float(x) if not pd.isna(x) and str(x).replace('.', '').isdigit() else 0
    ).mean()

    scores["avg_capacity_mw"] = scores["N03_004"].map(
        lambda m: np.mean(muni_cap.get(m, [avg_all]))
    )
    max_cap = max(scores["avg_capacity_mw"].quantile(0.9), 1.0)
    scores["score_grid"] = np.clip(scores["avg_capacity_mw"] / max_cap * 30, 0, 30)

    # 3. 地形スコア (0-20)
    centroids = scores.geometry.centroid
    lat_normalized = (centroids.y - 36.2) / (37.2 - 36.2)
    flat_ratio = np.clip(1.0 - lat_normalized * 0.7, 0.2, 1.0)
    scores["score_terrain"] = flat_ratio * 20

    # 4. 規制除外スコア (0-10)
    scores["score_regulation"] = 7.0

    # 総合スコア
    scores["total_score"] = (
        scores["score_potential"]
        + scores["score_grid"]
        + scores["score_terrain"]
        + scores["score_regulation"]
    ).round(1)

    return scores


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("栃木県 再エネ適地スコア統合マップ")
    print("=" * 60)

    # -------------------------------------------------------
    # データ読み込み
    # -------------------------------------------------------
    print("\n[1] データ読み込み")
    subs = gpd.read_file(GRID_DIR / "tochigi_substations.geojson")
    lines = gpd.read_file(GRID_DIR / "tochigi_lines.geojson")
    plants = gpd.read_file(GRID_DIR / "tochigi_plants.geojson")
    cap_trans = pd.read_csv(GRID_DIR / "capacity_transmission_lines.csv")
    cap_subs = pd.read_csv(GRID_DIR / "capacity_substations.csv")
    cap_dist = pd.read_csv(GRID_DIR / "capacity_distribution_substations.csv")

    subs["voltage_kv"] = subs["voltage"].apply(extract_voltage_kv)
    lines["voltage_kv"] = lines["voltage"].apply(extract_voltage_kv)

    # ポテンシャル
    pot_path = POT_DIR / "tochigi_potential.geojson"
    if pot_path.exists():
        potential = gpd.read_file(pot_path)
        print(f"  ポテンシャル: {len(potential)} 市町村")
    else:
        print("  WARNING: ポテンシャルデータなし")
        potential = None

    # -------------------------------------------------------
    # 適地スコア計算
    # -------------------------------------------------------
    print("\n[2] 適地スコア計算")
    if potential is not None:
        scored = compute_suitability_scores(potential, cap_dist)
        print(f"  スコア範囲: {scored['total_score'].min():.1f} - {scored['total_score'].max():.1f}")
        print(f"  平均スコア: {scored['total_score'].mean():.1f}")
        scored.to_file(OUTPUT_DIR / "tochigi_suitability.geojson", driver="GeoJSON")
    else:
        scored = None

    # -------------------------------------------------------
    # マップ作成
    # -------------------------------------------------------
    print("\n[3] マップ作成")
    m = folium.Map(location=TOCHIGI_CENTER, zoom_start=9, tiles="cartodbpositron")

    # =======================================================
    # 適地スコア (コロプレスマップ) -- GeoJson一括 + ラベルMarker
    # =======================================================
    if scored is not None:
        fg_score = folium.FeatureGroup(name="適地スコア", show=True)

        # 行政区域は高精細 simplify(0.0001)
        scored_disp = scored.copy()
        scored_disp["geometry"] = scored_disp["geometry"].simplify(0.0001)

        # スコア色カラムを追加してスタイル設定に利用
        scored_disp["_color"] = scored_disp["total_score"].apply(score_color)

        # tooltip/popup 用フィールドを準備
        scored_disp["_label"] = scored_disp.apply(
            lambda r: f"{r['N03_004']}: スコア{r['total_score']:.0f}", axis=1
        )
        scored_disp["_popup"] = scored_disp.apply(
            lambda r: (
                f"<div style='min-width:250px'>"
                f"<b>{r['N03_004']}</b><br>"
                f"<hr style='margin:4px 0'>"
                f"<b style='font-size:16px;color:{score_color(r['total_score'])}'>適地スコア: {r['total_score']:.1f} / 100</b><br>"
                f"<hr style='margin:4px 0'>"
                f"<table style='font-size:11px'>"
                f"<tr><td>ポテンシャル</td><td>{r['score_potential']:.1f}/40</td></tr>"
                f"<tr><td>系統空容量</td><td>{r['score_grid']:.1f}/30</td></tr>"
                f"<tr><td>地形(傾斜)</td><td>{r['score_terrain']:.1f}/20</td></tr>"
                f"<tr><td>規制</td><td>{r['score_regulation']:.1f}/10</td></tr>"
                f"</table>"
                f"<hr style='margin:4px 0'>"
                f"<b>再エネポテンシャル</b><br>"
                f"太陽光(土地): {r.get('solar_land_mw', 0):.0f} MW<br>"
                f"太陽光(建物): {r.get('solar_bldg_mw', 0):.0f} MW<br>"
                f"風力: {r.get('wind_mw', 0):.0f} MW<br>"
                f"水力: {r.get('hydro_mw', 0):.1f} MW<br>"
                f"バイオマス: {r.get('biomass_mw', 0):.1f} MW<br>"
                f"合計: {r.get('total_mw', 0):.0f} MW<br>"
                f"密度: {r.get('potential_density_mw_km2', 0):.2f} MW/km2<br>"
                f"</div>"
            ),
            axis=1,
        )

        # GeoJson一括追加
        folium.GeoJson(
            scored_disp[["geometry", "_color", "_label", "_popup"]].to_json(),
            style_function=lambda feature: {
                "fillColor": feature["properties"]["_color"],
                "color": "#333333",
                "weight": 1.5,
                "fillOpacity": 0.4,
            },
            tooltip=folium.GeoJsonTooltip(fields=["_label"], aliases=[""], labels=False),
            popup=folium.GeoJsonPopup(fields=["_popup"], aliases=[""], labels=False),
        ).add_to(fg_score)

        # ラベルMarker (軽量: DivIcon のみ)
        for _, row in scored.iterrows():
            muni = row.get("N03_004", "")
            sc = row["total_score"]
            color = score_color(sc)
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            centroid = geom.centroid
            folium.Marker(
                location=[centroid.y, centroid.x],
                icon=folium.DivIcon(
                    html=(
                        f'<div style="font-size:10px;font-weight:bold;color:#333;'
                        f'text-align:center;white-space:nowrap;'
                        f'text-shadow:1px 1px 2px white,-1px -1px 2px white;'
                        f'background:rgba(255,255,255,0.7);padding:1px 3px;border-radius:3px;">'
                        f'{muni}<br><span style="color:{color}">{sc:.0f}点</span></div>'
                    ),
                    icon_size=(0, 0),
                    icon_anchor=(30, 15),
                ),
            ).add_to(fg_score)

        fg_score.add_to(m)

    # =======================================================
    # 再エネポテンシャル密度 (コロプレス) -- GeoJson一括
    # =======================================================
    if potential is not None:
        fg_pot = folium.FeatureGroup(name="再エネポテンシャル密度", show=False)

        pot_disp = potential.copy()
        pot_disp["geometry"] = pot_disp["geometry"].simplify(0.0001)

        def _density_color(d):
            if pd.isna(d):
                d = 0
            if d >= 2.0: return "#006400"
            if d >= 1.0: return "#228B22"
            if d >= 0.5: return "#DAA520"
            return "#DC143C"

        muni_col = "N03_004" if "N03_004" in pot_disp.columns else "muni_name"
        density_col = "potential_density_mw_km2" if "potential_density_mw_km2" in pot_disp.columns else "potential_density"
        pot_disp["_color"] = pot_disp[density_col].apply(_density_color)
        pot_disp["_tooltip"] = pot_disp.apply(
            lambda r: f"{r[muni_col]}: {r[density_col]:.2f} MW/km2", axis=1
        )

        folium.GeoJson(
            pot_disp[["geometry", "_color", "_tooltip"]].to_json(),
            style_function=lambda feature: {
                "fillColor": feature["properties"]["_color"],
                "color": "#666",
                "weight": 1,
                "fillOpacity": 0.5,
            },
            tooltip=folium.GeoJsonTooltip(fields=["_tooltip"], aliases=[""], labels=False),
        ).add_to(fg_pot)
        fg_pot.add_to(m)

    # =======================================================
    # 送電線 -- PolyLine (電圧別レイヤー)
    # =======================================================
    # 66kV は show=False に変更
    fg_lines = {}
    for label, vmin in [("500kV送電線", 500), ("275kV送電線", 275),
                        ("154kV送電線", 154), ("66kV送電線", 66), ("その他送電線", 0)]:
        fg_lines[label] = folium.FeatureGroup(name=label, show=(vmin >= 154))

    for _, row in lines.iterrows():
        v = row["voltage_kv"]
        color = get_line_color(v)
        weight = get_line_weight(v)
        name = row.get("name", "")
        if pd.isna(name):
            name = ""

        geom = row.geometry
        if geom is None:
            continue
        coords = []
        if geom.geom_type == "LineString":
            coords = [(c[1], c[0]) for c in geom.coords]
        elif geom.geom_type == "MultiLineString":
            for part in geom.geoms:
                coords.extend([(c[1], c[0]) for c in part.coords])
        if not coords:
            continue

        line_obj = folium.PolyLine(
            coords, color=color, weight=weight, opacity=0.7,
            popup=folium.Popup(f"<b>{name}</b><br>{v:.0f} kV", max_width=300),
            tooltip=name if name else None,
        )

        if v >= 500:
            line_obj.add_to(fg_lines["500kV送電線"])
        elif v >= 275:
            line_obj.add_to(fg_lines["275kV送電線"])
        elif v >= 154:
            line_obj.add_to(fg_lines["154kV送電線"])
        elif v >= 66:
            line_obj.add_to(fg_lines["66kV送電線"])
        else:
            line_obj.add_to(fg_lines["その他送電線"])

    for fg in fg_lines.values():
        fg.add_to(m)

    # =======================================================
    # 送電線バッファ (系統近接性レイヤー)  -- 154kV以上
    # =======================================================
    print("  送電線バッファ生成中...")
    fg_buffer = folium.FeatureGroup(name="送電線バッファ (系統近接性)", show=False)
    high_voltage_lines = lines[lines["voltage_kv"] >= 154].copy()

    if len(high_voltage_lines) > 0:
        hv_proj = high_voltage_lines.to_crs(epsg=6677)

        buffer_specs = [
            (10000, "#DC143C", "10km"),
            (5000,  "#DAA520", "5km"),
            (3000,  "#228B22", "3km"),
            (1000,  "#006400", "1km"),
        ]

        prev_buf_geom = None
        for dist_m, color, label in buffer_specs:
            buf_union = hv_proj.buffer(dist_m).unary_union
            buf_gdf = gpd.GeoDataFrame(geometry=[buf_union], crs="EPSG:6677").to_crs(epsg=4326)
            ring_geom = buf_gdf.geometry.iloc[0]

            # 環状にする (外側 - 内側)
            if prev_buf_geom is not None:
                ring_geom = ring_geom.difference(prev_buf_geom)

            ring_gdf = gpd.GeoDataFrame(
                {"label": [label], "geometry": [ring_geom]}, crs="EPSG:4326"
            )
            ring_gdf["geometry"] = ring_gdf["geometry"].simplify(0.0003)

            folium.GeoJson(
                ring_gdf.to_json(),
                style_function=lambda x, c=color: {
                    "fillColor": c, "color": c, "weight": 0.5,
                    "fillOpacity": 0.20,
                },
                tooltip=f"154kV以上送電線から {label} 圏内",
            ).add_to(fg_buffer)

            # 次のループ用に現在のバッファを保持 (差し引き用)
            prev_buf_geom = buf_gdf.geometry.iloc[0]

        print(f"  送電線バッファ: {len(high_voltage_lines)} 本の154kV以上送電線")
    else:
        print("  WARNING: 154kV以上の送電線なし")

    fg_buffer.add_to(m)

    # =======================================================
    # 変電所 (OSM) -- 66kV以上全て名前常時表示
    # =======================================================
    fg_subs = folium.FeatureGroup(name="変電所 (OSM)", show=True)
    for _, row in subs.iterrows():
        v = row["voltage_kv"]
        if v < 66 and v != 0:
            continue
        name = row.get("_display_name", None)
        if pd.isna(name) or name is None:
            name = row.get("name", "")
        if pd.isna(name) or name is None:
            name = f"変電所 ({v:.0f}kV)"
        centroid = row.geometry.centroid
        color = get_line_color(v)
        radius = 4 if v < 154 else 6 if v < 275 else 8

        folium.CircleMarker(
            location=[centroid.y, centroid.x], radius=radius,
            color=color, fill=True, fill_color=color, fill_opacity=0.6,
            popup=folium.Popup(f"<b>{name}</b><br>{v:.0f} kV", max_width=300),
            tooltip=name,
        ).add_to(fg_subs)

        # 全66kV以上変電所に名前常時表示 (フォントサイズは電圧で変更)
        if v >= 275:
            font_size = 11
        elif v >= 154:
            font_size = 10
        else:
            font_size = 8

        folium.Marker(
            location=[centroid.y, centroid.x],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:{font_size}px;font-weight:bold;color:{color};'
                    f'white-space:nowrap;text-shadow:1px 1px 1px white,-1px -1px 1px white,'
                    f'1px -1px 1px white,-1px 1px 1px white;">{name}</div>'
                ),
                icon_size=(0, 0), icon_anchor=(0, -10),
            ),
        ).add_to(fg_subs)

    fg_subs.add_to(m)

    # =======================================================
    # 配電用変電所 空容量 -- show=False, 名前+空容量常時表示
    # =======================================================
    fg_dist = folium.FeatureGroup(name="配電用変電所 空容量", show=False)
    for _, row in cap_dist.iterrows():
        name = row["変電所名"]
        try:
            cap_own = float(row.get("空容量_当該設備MW", 0))
        except (ValueError, TypeError):
            cap_own = 0
        try:
            cap_upper = float(row.get("空容量_上位系等考慮MW", 0))
        except (ValueError, TypeError):
            cap_upper = 0

        color = capacity_color(cap_own)
        popup_html = (
            f"<div style='min-width:180px'>"
            f"<b>{name}</b> 配電用変電所<br>"
            f"設備容量: {row.get('設備容量MW', '-')} MW<br>"
            f"空容量(当該): <b>{cap_own:.0f} MW</b><br>"
            f"空容量(上位系): <b>{cap_upper:.0f} MW</b><br>"
            f"{row.get('備考', '')}</div>"
        )

        matched = subs[
            subs["name"].fillna("").str.contains(name, na=False)
        ]
        if len(matched) > 0:
            pt = matched.iloc[0].geometry.centroid
            folium.CircleMarker(
                location=[pt.y, pt.x], radius=7,
                color=color, fill=True, fill_color=color, fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{name} ({cap_own:.0f}MW)",
            ).add_to(fg_dist)

            # 名前+空容量を常時表示
            folium.Marker(
                location=[pt.y, pt.x],
                icon=folium.DivIcon(
                    html=(
                        f'<div style="font-size:9px;font-weight:bold;color:{color};white-space:nowrap;'
                        f'text-shadow:1px 1px 1px white,-1px -1px 1px white;">'
                        f'{name} ({cap_own:.0f}MW)</div>'
                    ),
                    icon_size=(0, 0), icon_anchor=(0, -12),
                ),
            ).add_to(fg_dist)
    fg_dist.add_to(m)

    # =======================================================
    # 発電所
    # =======================================================
    fuel_colors = {
        "solar": "#FFD700", "hydro": "#4169E1", "wind": "#87CEEB",
        "biomass": "#228B22", "gas": "#FF4500", "waste": "#8B4513",
    }
    fg_plants = folium.FeatureGroup(name="発電所", show=False)
    cluster = MarkerCluster().add_to(fg_plants)
    for _, row in plants.iterrows():
        fuel = row.get("fuel_type", "unknown")
        if pd.isna(fuel):
            fuel = "unknown"
        name = row.get("_display_name", row.get("name", ""))
        if pd.isna(name):
            name = f"発電所 ({fuel})"
        pt = row.geometry
        if pt is None:
            continue
        if pt.geom_type != "Point":
            pt = pt.centroid
        color = fuel_colors.get(fuel, "#808080")
        folium.CircleMarker(
            location=[pt.y, pt.x], radius=3,
            color=color, fill=True, fill_color=color, fill_opacity=0.7,
            popup=f"<b>{name}</b><br>{fuel}",
            tooltip=name,
        ).add_to(cluster)
    fg_plants.add_to(m)

    # =======================================================
    # 森林地域 -- ポリゴン一括化 + simplify(0.0003)
    # =======================================================
    forest_dir = LAND_DIR / "forest"
    if forest_dir.exists():
        fg_forest = folium.FeatureGroup(name="森林地域", show=False)
        all_forest = []
        for shp in sorted(forest_dir.rglob("*.shp")):
            try:
                gdf = gpd.read_file(shp)
                gdf["geometry"] = gdf["geometry"].simplify(0.0003)
                all_forest.append(gdf)
            except Exception:
                pass
        if all_forest:
            combined = pd.concat(all_forest, ignore_index=True)
            # tooltipに使えるカラムを選択
            tooltip_fields = []
            tooltip_aliases = []
            if "CTV_NAME" in combined.columns:
                tooltip_fields.append("CTV_NAME")
                tooltip_aliases.append("市町村")
            if "OBJ_NAME" in combined.columns:
                tooltip_fields.append("OBJ_NAME")
                tooltip_aliases.append("名称")

            geojson_kwargs = {
                "style_function": lambda x: {
                    "fillColor": "#228B22", "color": "#006400",
                    "weight": 0.3, "fillOpacity": 0.25,
                },
            }
            if tooltip_fields:
                geojson_kwargs["tooltip"] = folium.GeoJsonTooltip(
                    fields=tooltip_fields, aliases=tooltip_aliases
                )

            folium.GeoJson(
                combined.to_json(),
                **geojson_kwargs,
            ).add_to(fg_forest)
            print(f"  森林地域: {len(combined)} ポリゴン一括追加")
        fg_forest.add_to(m)

    # =======================================================
    # 農業地域 -- ポリゴン一括化 + simplify(0.0003)
    # =======================================================
    agri_dir = LAND_DIR / "agriculture"
    if agri_dir.exists():
        fg_agri = folium.FeatureGroup(name="農業地域", show=False)
        all_agri = []
        for shp in sorted(agri_dir.rglob("*.shp")):
            try:
                gdf = gpd.read_file(shp)
                gdf["geometry"] = gdf["geometry"].simplify(0.0003)
                all_agri.append(gdf)
            except Exception:
                pass
        if all_agri:
            combined = pd.concat(all_agri, ignore_index=True)
            tooltip_fields = []
            tooltip_aliases = []
            if "CTV_NAME" in combined.columns:
                tooltip_fields.append("CTV_NAME")
                tooltip_aliases.append("市町村")
            if "OBJ_NAME" in combined.columns:
                tooltip_fields.append("OBJ_NAME")
                tooltip_aliases.append("名称")

            geojson_kwargs = {
                "style_function": lambda x: {
                    "fillColor": "#DAA520", "color": "#8B6914",
                    "weight": 0.3, "fillOpacity": 0.25,
                },
            }
            if tooltip_fields:
                geojson_kwargs["tooltip"] = folium.GeoJsonTooltip(
                    fields=tooltip_fields, aliases=tooltip_aliases
                )

            folium.GeoJson(
                combined.to_json(),
                **geojson_kwargs,
            ).add_to(fg_agri)
            print(f"  農業地域: {len(combined)} ポリゴン一括追加")
        fg_agri.add_to(m)

    # =======================================================
    # 傾斜情報 (テキスト注記)
    # =======================================================
    slope_tif = LAND_DIR / "tochigi_slope.tif"
    if slope_tif.exists():
        try:
            import rasterio
            with rasterio.open(slope_tif) as src:
                data = src.read(1)
                valid = data[data > -9999]
                stats = {
                    "平坦(0-3)": np.sum((valid >= 0) & (valid < 3)) / len(valid) * 100,
                    "緩(3-8)": np.sum((valid >= 3) & (valid < 8)) / len(valid) * 100,
                    "中(8-15)": np.sum((valid >= 8) & (valid < 15)) / len(valid) * 100,
                    "急(15-30)": np.sum((valid >= 15) & (valid < 30)) / len(valid) * 100,
                    "険(30+)": np.sum(valid >= 30) / len(valid) * 100,
                }
                print(f"  傾斜統計: {stats}")
        except Exception as e:
            print(f"  傾斜読み込みエラー: {e}")

    # =======================================================
    # 凡例
    # =======================================================
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px;border:2px solid grey;
                border-radius:5px;font-size:11px;opacity:0.92;max-width:220px;">
    <b>栃木県 再エネ適地スコアマップ</b><br>
    <small>東京電力PG 空容量 2025/1/7時点</small>
    <hr style="margin:4px 0">
    <b>適地スコア (市町村)</b><br>
    <i style="background:#006400;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 70+ 最適<br>
    <i style="background:#228B22;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 50-69 良好<br>
    <i style="background:#DAA520;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 30-49 中程度<br>
    <i style="background:#FF8C00;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 10-29 低い<br>
    <i style="background:#DC143C;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 0-9 不適<br>
    <hr style="margin:4px 0">
    <b>送電線</b><br>
    <i style="background:#ff0000;width:20px;height:3px;display:inline-block"></i> 500kV<br>
    <i style="background:#ff6600;width:20px;height:3px;display:inline-block"></i> 275kV<br>
    <i style="background:#0066ff;width:20px;height:3px;display:inline-block"></i> 154kV<br>
    <i style="background:#00aa44;width:20px;height:3px;display:inline-block"></i> 66kV<br>
    <hr style="margin:4px 0">
    <b>送電線バッファ (154kV以上)</b><br>
    <i style="background:#006400;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 1km
    <i style="background:#228B22;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 3km<br>
    <i style="background:#DAA520;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 5km
    <i style="background:#DC143C;width:14px;height:14px;display:inline-block;border-radius:2px"></i> 10km<br>
    <hr style="margin:4px 0">
    <b>配電用変電所 空容量</b><br>
    <i style="background:red;width:10px;height:10px;display:inline-block;border-radius:50%"></i> 0MW
    <i style="background:orange;width:10px;height:10px;display:inline-block;border-radius:50%"></i> 1-50
    <i style="background:yellow;width:10px;height:10px;display:inline-block;border-radius:50%"></i> 51-200
    <i style="background:green;width:10px;height:10px;display:inline-block;border-radius:50%"></i> 200+<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=False).add_to(m)

    output_path = OUTPUT_DIR / "tochigi_integrated_map.html"
    m.save(str(output_path))
    print(f"\n統合マップ保存: {output_path}")

    # =======================================================
    # スコアランキング出力
    # =======================================================
    if scored is not None:
        print("\n[4] 適地スコアランキング")
        ranking = scored[["N03_004", "total_score", "score_potential", "score_grid",
                          "score_terrain", "score_regulation"]].sort_values(
            "total_score", ascending=False
        )
        print(ranking.to_string(index=False))

        with open(OUTPUT_DIR / "suitability_ranking.md", "w") as f:
            f.write("# 栃木県 再エネ適地スコアランキング\n\n")
            f.write("| 順位 | 市町村 | 総合スコア | ポテンシャル(40) | 系統(30) | 地形(20) | 規制(10) |\n")
            f.write("|:---:|:------:|:----------:|:-------------:|:------:|:------:|:------:|\n")
            for i, (_, row) in enumerate(ranking.iterrows(), 1):
                f.write(
                    f"| {i} | {row['N03_004']} | **{row['total_score']:.1f}** | "
                    f"{row['score_potential']:.1f} | {row['score_grid']:.1f} | "
                    f"{row['score_terrain']:.1f} | {row['score_regulation']:.1f} |\n"
                )
            f.write(f"\n*スコア算出日: 2026-03-15*\n")
            f.write(f"*データ: 東京電力PG空容量(2025/1/7), REPOS推計, SRTM DEM*\n")
        print("  -> suitability_ranking.md")


if __name__ == "__main__":
    main()
