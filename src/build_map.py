"""
栃木県 再エネポテンシャル・系統空容量 統合マップ生成
Folium による HTML インタラクティブマップ
"""
import json
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRID_DIR = PROJECT_ROOT / "data" / "grid"
OUTPUT_DIR = PROJECT_ROOT / "output"

# 栃木県中心座標
TOCHIGI_CENTER = [36.65, 139.88]

# 電圧別の色
VOLTAGE_COLORS = {
    500: "#ff0000",
    275: "#ff6600",
    154: "#0066ff",
    66: "#00aa44",
    22: "#886600",
}


def get_line_color(voltage_kv):
    if voltage_kv >= 500:
        return VOLTAGE_COLORS[500]
    elif voltage_kv >= 275:
        return VOLTAGE_COLORS[275]
    elif voltage_kv >= 154:
        return VOLTAGE_COLORS[154]
    elif voltage_kv >= 66:
        return VOLTAGE_COLORS[66]
    else:
        return "#999999"


def get_line_weight(voltage_kv):
    if voltage_kv >= 500:
        return 4
    elif voltage_kv >= 275:
        return 3
    elif voltage_kv >= 154:
        return 2.5
    elif voltage_kv >= 66:
        return 1.5
    else:
        return 1


def capacity_color(value):
    """空容量 → 色 (赤=0, 黄=少, 緑=多)"""
    if pd.isna(value) or value <= 0:
        return "red"
    elif value <= 50:
        return "orange"
    elif value <= 200:
        return "yellow"
    else:
        return "green"


def extract_voltage_kv(v):
    if pd.isna(v) or v == "":
        return 0
    try:
        val = float(str(v).replace(",", ""))
        return val / 1000 if val > 1000 else val
    except ValueError:
        return 0


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # データ読み込み
    print("Loading data...")
    subs = gpd.read_file(GRID_DIR / "tochigi_substations.geojson")
    lines = gpd.read_file(GRID_DIR / "tochigi_lines.geojson")
    plants = gpd.read_file(GRID_DIR / "tochigi_plants.geojson")

    cap_trans = pd.read_csv(GRID_DIR / "capacity_transmission_lines.csv")
    cap_subs = pd.read_csv(GRID_DIR / "capacity_substations.csv")
    cap_dist = pd.read_csv(GRID_DIR / "capacity_distribution_substations.csv")

    subs["voltage_kv"] = subs["voltage"].apply(extract_voltage_kv)
    lines["voltage_kv"] = lines["voltage"].apply(extract_voltage_kv)

    # マップ作成
    print("Building map...")
    m = folium.Map(
        location=TOCHIGI_CENTER,
        zoom_start=9,
        tiles="cartodbpositron",
    )

    # === レイヤー1: 送電線 ===
    fg_lines = {}
    for label, vmin in [("500kV送電線", 500), ("275kV送電線", 275),
                        ("154kV送電線", 154), ("66kV送電線", 66), ("その他送電線", 0)]:
        fg_lines[label] = folium.FeatureGroup(name=label, show=(vmin >= 66))

    for _, row in lines.iterrows():
        v = row["voltage_kv"]
        color = get_line_color(v)
        weight = get_line_weight(v)

        name = row.get("_display_name", row.get("name", ""))
        if pd.isna(name):
            name = ""
        popup_text = f"<b>{name}</b><br>電圧: {v:.0f} kV"

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

        line = folium.PolyLine(
            coords, color=color, weight=weight, opacity=0.7,
            popup=folium.Popup(popup_text, max_width=300),
            tooltip=name if name else None,
        )

        if v >= 500:
            line.add_to(fg_lines["500kV送電線"])
        elif v >= 275:
            line.add_to(fg_lines["275kV送電線"])
        elif v >= 154:
            line.add_to(fg_lines["154kV送電線"])
        elif v >= 66:
            line.add_to(fg_lines["66kV送電線"])
        else:
            line.add_to(fg_lines["その他送電線"])

    for fg in fg_lines.values():
        fg.add_to(m)

    # === レイヤー2: 変電所 (OSM) ===
    fg_subs = folium.FeatureGroup(name="変電所 (OSM)", show=True)
    for _, row in subs.iterrows():
        v = row["voltage_kv"]
        if v < 66 and v != 0:
            continue  # 低圧は省略 (v==0は電圧不明、含める)
        name = row.get("_display_name", row.get("name", ""))
        if pd.isna(name):
            name = f"変電所 ({v:.0f}kV)"

        centroid = row.geometry.centroid
        color = get_line_color(v)
        radius = 4 if v < 154 else 6 if v < 275 else 8

        folium.CircleMarker(
            location=[centroid.y, centroid.x],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.6,
            popup=folium.Popup(f"<b>{name}</b><br>電圧: {v:.0f} kV", max_width=300),
            tooltip=name,
        ).add_to(fg_subs)

        # 変電所名を常時表示（154kV以上）
        if v >= 154:
            folium.Marker(
                location=[centroid.y, centroid.x],
                icon=folium.DivIcon(
                    html=f'<div style="font-size:9px;font-weight:bold;color:{color};'
                         f'white-space:nowrap;text-shadow:1px 1px 1px white,-1px -1px 1px white,'
                         f'1px -1px 1px white,-1px 1px 1px white;">{name}</div>',
                    icon_size=(0, 0),
                    icon_anchor=(0, -10),
                ),
            ).add_to(fg_subs)
    fg_subs.add_to(m)

    # === レイヤー3: 空容量 - 送電線 ===
    # (空容量CSVにはジオメトリがないが、名称マッチングで注釈表示)

    # === レイヤー4: 空容量 - 配電用変電所 ===
    fg_dist = folium.FeatureGroup(name="配電用変電所 空容量", show=True)
    for _, row in cap_dist.iterrows():
        name = row["変電所名"]
        cap_own = row.get("空容量_当該設備MW", 0)
        cap_upper = row.get("空容量_上位系等考慮MW", 0)
        try:
            cap_own = float(cap_own) if not pd.isna(cap_own) else 0
        except (ValueError, TypeError):
            cap_own = 0
        try:
            cap_upper = float(cap_upper) if not pd.isna(cap_upper) else 0
        except (ValueError, TypeError):
            cap_upper = 0

        color = capacity_color(cap_own)
        remarks = row.get("備考", "")

        popup_html = f"""
        <div style="min-width:200px">
        <b>{name}</b> 配電用変電所<br>
        <hr style="margin:4px 0">
        一次電圧: {row.get('電圧kV_一次', '-')} kV<br>
        台数: {row.get('台数', '-')}<br>
        設備容量: {row.get('設備容量MW', '-')} MW<br>
        運用容量: {row.get('運用容量値MW', '-')} MW<br>
        <hr style="margin:4px 0">
        <b>空容量(当該): {cap_own:.0f} MW</b><br>
        <b>空容量(上位系考慮): {cap_upper:.0f} MW</b><br>
        N-1電制: {row.get('N1電制_適用可否', '-')}<br>
        出力制御: {row.get('平常時出力制御の可能性', '-')}<br>
        <hr style="margin:4px 0">
        <small>{remarks}</small>
        </div>
        """

        # 配電用変電所の位置は不明なので、名称から変電所GeoJSONでマッチング試行
        matched = subs[
            subs["_display_name"].fillna("").str.contains(name, na=False) |
            subs["name"].fillna("").str.contains(name, na=False)
        ]
        if len(matched) > 0:
            pt = matched.iloc[0].geometry.centroid
            tooltip_text = f"{name} ({cap_own:.0f}MW)"
            folium.CircleMarker(
                location=[pt.y, pt.x],
                radius=8,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=350),
                tooltip=tooltip_text,
            ).add_to(fg_dist)
            # 配電用変電所名を常時表示
            folium.Marker(
                location=[pt.y, pt.x],
                icon=folium.DivIcon(
                    html=f'<div style="font-size:8px;color:{color};'
                         f'white-space:nowrap;text-shadow:1px 1px 1px white,-1px -1px 1px white,'
                         f'1px -1px 1px white,-1px 1px 1px white;">{name}</div>',
                    icon_size=(0, 0),
                    icon_anchor=(0, -12),
                ),
            ).add_to(fg_dist)
    fg_dist.add_to(m)

    # === レイヤー5: 発電所 ===
    fuel_colors = {
        "solar": "#FFD700",
        "hydro": "#4169E1",
        "wind": "#87CEEB",
        "biomass": "#228B22",
        "gas": "#FF4500",
        "waste": "#8B4513",
        "nuclear": "#800080",
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
        cap_mw = row.get("capacity_mw", "")

        color = fuel_colors.get(fuel, "#808080")
        pt = row.geometry
        if pt is None:
            continue
        if pt.geom_type != "Point":
            pt = pt.centroid

        popup_text = f"<b>{name}</b><br>燃料: {fuel}"
        if cap_mw and not pd.isna(cap_mw):
            popup_text += f"<br>容量: {cap_mw} MW"

        folium.CircleMarker(
            location=[pt.y, pt.x],
            radius=3,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_text, max_width=250),
        ).add_to(cluster)
    fg_plants.add_to(m)

    # === レイヤー6: 空容量サマリーテーブル (特高変電所) ===
    fg_cap_subs = folium.FeatureGroup(name="特高変電所 空容量", show=False)
    for _, row in cap_subs.iterrows():
        name = row["変電所名"]
        cap_own = row.get("空容量_当該設備MW", 0)
        try:
            cap_own = float(cap_own) if not pd.isna(cap_own) else 0
        except (ValueError, TypeError):
            cap_own = 0

        color = capacity_color(cap_own)
        voltage = row.get("電圧kV", "")

        popup_html = f"""
        <div style="min-width:200px">
        <b>{name}</b> ({voltage}kV 変電所)<br>
        設備容量: {row.get('設備容量MW', '-')} MW<br>
        運用容量: {row.get('運用容量値MW', '-')} MW<br>
        <b>空容量(当該): {cap_own:.0f} MW</b><br>
        空容量(上位系考慮): {row.get('空容量_上位系等考慮MW', '-')} MW<br>
        N-1電制: {row.get('N1電制_適用可否', '-')}<br>
        {row.get('備考', '')}
        </div>
        """

        matched = subs[
            subs["_display_name"].fillna("").str.contains(name, na=False) |
            subs["name"].fillna("").str.contains(name, na=False)
        ]
        if len(matched) > 0:
            pt = matched.iloc[0].geometry.centroid
            tooltip_text = f"{name} ({voltage}kV, 空容量{cap_own:.0f}MW)"
            folium.CircleMarker(
                location=[pt.y, pt.x],
                radius=10,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=folium.Popup(popup_html, max_width=350),
                tooltip=tooltip_text,
                weight=3,
            ).add_to(fg_cap_subs)
            # 特高変電所名を常時表示
            folium.Marker(
                location=[pt.y, pt.x],
                icon=folium.DivIcon(
                    html=f'<div style="font-size:10px;font-weight:bold;color:{color};'
                         f'white-space:nowrap;text-shadow:1px 1px 2px white,-1px -1px 2px white,'
                         f'1px -1px 2px white,-1px 1px 2px white;">{name} ({cap_own:.0f}MW)</div>',
                    icon_size=(0, 0),
                    icon_anchor=(0, -14),
                ),
            ).add_to(fg_cap_subs)
    fg_cap_subs.add_to(m)

    # === レイヤー7: 森林地域 ===
    forest_dir = PROJECT_ROOT / "data" / "land" / "forest"
    if forest_dir.exists():
        fg_forest = folium.FeatureGroup(name="森林地域", show=False)
        for shp in sorted(forest_dir.rglob("*.shp")):
            try:
                gdf = gpd.read_file(shp)
                # Simplify geometry for performance
                gdf["geometry"] = gdf["geometry"].simplify(0.001)
                for _, row in gdf.iterrows():
                    geom = row.geometry
                    if geom is None or geom.is_empty:
                        continue
                    name = row.get("OBJ_NAME", "森林地域")
                    folium.GeoJson(
                        geom.__geo_interface__,
                        style_function=lambda x: {
                            "fillColor": "#228B22",
                            "color": "#006400",
                            "weight": 0.5,
                            "fillOpacity": 0.3,
                        },
                        popup=folium.Popup(f"{name}", max_width=200),
                    ).add_to(fg_forest)
            except Exception as e:
                print(f"  Warning: {shp.name}: {e}")
        fg_forest.add_to(m)
        print(f"  Added forest layer")

    # === レイヤー8: 農業地域 ===
    agri_dir = PROJECT_ROOT / "data" / "land" / "agriculture"
    if agri_dir.exists():
        fg_agri = folium.FeatureGroup(name="農業地域", show=False)
        for shp in sorted(agri_dir.rglob("*.shp")):
            try:
                gdf = gpd.read_file(shp)
                gdf["geometry"] = gdf["geometry"].simplify(0.001)
                for _, row in gdf.iterrows():
                    geom = row.geometry
                    if geom is None or geom.is_empty:
                        continue
                    name = row.get("OBJ_NAME", "農業地域")
                    folium.GeoJson(
                        geom.__geo_interface__,
                        style_function=lambda x: {
                            "fillColor": "#DAA520",
                            "color": "#8B6914",
                            "weight": 0.5,
                            "fillOpacity": 0.3,
                        },
                        popup=folium.Popup(f"{name}", max_width=200),
                    ).add_to(fg_agri)
            except Exception as e:
                print(f"  Warning: {shp.name}: {e}")
        fg_agri.add_to(m)
        print(f"  Added agriculture layer")

    # === レイヤー9: 行政区域境界 ===
    admin_dir = PROJECT_ROOT / "data" / "land" / "admin_boundary"
    if admin_dir.exists():
        fg_admin = folium.FeatureGroup(name="行政区域", show=True)
        for shp in sorted(admin_dir.rglob("*.shp")):
            try:
                gdf = gpd.read_file(shp)
                for _, row in gdf.iterrows():
                    geom = row.geometry
                    if geom is None or geom.is_empty:
                        continue
                    muni = row.get("N03_004", "")
                    if pd.isna(muni):
                        muni = row.get("N03_003", "")
                    folium.GeoJson(
                        geom.__geo_interface__,
                        style_function=lambda x: {
                            "fillColor": "transparent",
                            "color": "#333333",
                            "weight": 1.5,
                            "fillOpacity": 0,
                            "dashArray": "5,3",
                        },
                        popup=folium.Popup(f"{muni}", max_width=200),
                    ).add_to(fg_admin)
            except Exception as e:
                print(f"  Warning: {shp.name}: {e}")
        fg_admin.add_to(m)
        print(f"  Added admin boundary layer")

    # === 凡例 ===
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px;border:2px solid grey;
                border-radius:5px;font-size:12px;opacity:0.9;">
    <b>栃木県 系統空容量マップ</b><br>
    <hr style="margin:4px 0">
    <b>送電線</b><br>
    <i style="background:#ff0000;width:30px;height:3px;display:inline-block"></i> 500kV<br>
    <i style="background:#ff6600;width:30px;height:3px;display:inline-block"></i> 275kV<br>
    <i style="background:#0066ff;width:30px;height:3px;display:inline-block"></i> 154kV<br>
    <i style="background:#00aa44;width:30px;height:3px;display:inline-block"></i> 66kV<br>
    <hr style="margin:4px 0">
    <b>空容量</b><br>
    <i style="background:red;width:12px;height:12px;display:inline-block;border-radius:50%"></i> 0 MW<br>
    <i style="background:orange;width:12px;height:12px;display:inline-block;border-radius:50%"></i> 1-50 MW<br>
    <i style="background:yellow;width:12px;height:12px;display:inline-block;border-radius:50%"></i> 51-200 MW<br>
    <i style="background:green;width:12px;height:12px;display:inline-block;border-radius:50%"></i> 200+ MW<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # レイヤーコントロール
    folium.LayerControl(collapsed=False).add_to(m)

    # 保存
    output_path = OUTPUT_DIR / "tochigi_grid_map.html"
    m.save(str(output_path))
    print(f"\nMap saved to: {output_path}")
    print(f"Open in browser: file://{output_path}")


if __name__ == "__main__":
    main()
