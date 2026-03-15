#!/usr/bin/env python3
"""都賀変電所エリア詳細分析スクリプト

P_X蓄電所建売案件（栃木市都賀町家中、高圧配電線連系）に関する
系統制約の詳細分析を行う。
"""

import csv
import json
import math
import os
from pathlib import Path

import folium
from folium import plugins
import geopandas as gpd
from shapely.geometry import shape, Point

# ── パス設定 ──
BASE = Path(__file__).resolve().parent.parent
DATA_GRID = BASE / "data" / "grid"
DATA_LAND = BASE / "data" / "land" / "admin_boundary"
OUTPUT = BASE / "output"
OUTPUT.mkdir(exist_ok=True)


# ──────────────────────────────────────────────
# 1. データ読み込み
# ──────────────────────────────────────────────
def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


dist_subs = load_csv(DATA_GRID / "capacity_distribution_substations.csv")
trans_lines = load_csv(DATA_GRID / "capacity_transmission_lines.csv")
upper_subs = load_csv(DATA_GRID / "capacity_substations.csv")

with open(DATA_GRID / "tochigi_substations.geojson", encoding="utf-8") as f:
    osm_subs = json.load(f)
with open(DATA_GRID / "tochigi_lines.geojson", encoding="utf-8") as f:
    osm_lines = json.load(f)
with open(DATA_GRID / "tochigi_plants.geojson", encoding="utf-8") as f:
    osm_plants = json.load(f)


# ──────────────────────────────────────────────
# 2. 都賀変電所の特定
# ──────────────────────────────────────────────
tsuga_dist = next(r for r in dist_subs if r["No"] == "62" and "都賀" in r["変電所名"])
print(f"[配電用変電所] No.{tsuga_dist['No']} {tsuga_dist['変電所名']}")
print(f"  電圧: {tsuga_dist['電圧kV_一次']}/{tsuga_dist['電圧kV_二次']}kV")
print(f"  設備容量: {tsuga_dist['設備容量MW']}MW  運用容量: {tsuga_dist['運用容量値MW']}MW")
print(f"  空容量(設備): {tsuga_dist['空容量_当該設備MW']}MW  空容量(上位系考慮): {tsuga_dist['空容量_上位系等考慮MW']}MW")
print(f"  備考: {tsuga_dist['備考']}")

# OSM位置
tsuga_osm = None
for feat in osm_subs["features"]:
    name = feat["properties"].get("name") or ""
    if "都賀" in name and "変電所" in name:
        tsuga_osm = feat
        break

if tsuga_osm:
    geom = shape(tsuga_osm["geometry"])
    tsuga_center = (geom.centroid.y, geom.centroid.x)  # (lat, lon)
    print(f"  OSM位置: {tsuga_center[0]:.6f}, {tsuga_center[1]:.6f}")
else:
    tsuga_center = (36.40, 139.79)
    print(f"  OSM位置不明 → 推定座標: {tsuga_center}")


# ──────────────────────────────────────────────
# 3. 制約チェーン分析
# ──────────────────────────────────────────────
def parse_constraint_ref(biko):
    """備考欄から上位系制約の参照IDを抽出"""
    import re
    m = re.search(r"上位系[（(](.+?)[)）]による制約", biko)
    if m:
        return m.group(1)
    return None


def find_item_by_ref(ref):
    """参照IDから送電線/変電所を検索
    例: 送栃木66kV36 → 送電線 66kV No.36
        変栃木66kV4  → 変電所 66kV No.4
        変栃木154kV2 → 変電所 154kV No.2
        栃木154kV4   → 変電所 154kV No.4 (送/変省略)
        変3          → 上位変電所(特定困難)
        変4          → 上位変電所(特定困難)
    """
    import re
    # 送電線パターン: 送栃木66kV36
    m = re.match(r"送栃木(\d+)kV(\d+)", ref)
    if m:
        voltage, no = m.group(1), m.group(2)
        for row in trans_lines:
            if row["電圧kV"] == voltage and row["No"] == no:
                return {"type": "送電線", "voltage": voltage, "no": no, "name": row["送電線名"], "data": row}
        return {"type": "送電線", "voltage": voltage, "no": no, "name": f"(不明No.{no})", "data": None}

    # 変電所パターン: 変栃木66kV4 or 栃木154kV4
    m = re.match(r"変?栃木(\d+)kV(\d+)", ref)
    if m:
        voltage, no = m.group(1), m.group(2)
        for row in upper_subs:
            if row["電圧kV"] == voltage and row["No"] == no:
                return {"type": "変電所", "voltage": voltage, "no": no, "name": row["変電所名"], "data": row}
        return {"type": "変電所", "voltage": voltage, "no": no, "name": f"(不明No.{no})", "data": None}

    # 変3, 変4 など (基幹系統)
    m = re.match(r"変(\d+)", ref)
    if m:
        return {"type": "基幹系統", "voltage": "500/275", "no": m.group(1), "name": f"基幹系統バンク{m.group(1)}", "data": None}

    return {"type": "不明", "voltage": "-", "no": "-", "name": ref, "data": None}


def trace_constraints(start_biko, depth=0, visited=None):
    """制約チェーンを再帰的に追跡"""
    if visited is None:
        visited = set()
    chain = []
    ref = parse_constraint_ref(start_biko)
    if not ref or ref in visited:
        return chain
    visited.add(ref)

    item = find_item_by_ref(ref)
    chain.append(item)

    if item["data"]:
        next_biko = item["data"].get("備考", "")
        if next_biko:
            chain.extend(trace_constraints(next_biko, depth + 1, visited))

    return chain


print("\n=== 制約チェーン分析 ===")
constraint_chain = trace_constraints(tsuga_dist["備考"])
print(f"\n都賀変電所(配電用No.62)")
for i, item in enumerate(constraint_chain):
    prefix = "  " + "→ " * (i + 1)
    if item["data"]:
        d = item["data"]
        cap_own = d.get("空容量_当該設備MW", "-")
        cap_upper = d.get("空容量_上位系等考慮MW", "-")
        name_key = "送電線名" if item["type"] == "送電線" else "変電所名"
        name = d.get(name_key, item["name"])
        biko = d.get("備考", "")
        print(f"{prefix}{item['type']} {item['voltage']}kV No.{item['no']} [{name}] "
              f"空容量(設備)={cap_own}MW 空容量(上位系)={cap_upper}MW")
        if biko:
            print(f"  {prefix}  備考: {biko}")
    else:
        print(f"{prefix}{item['type']} {item['voltage']}kV [{item['name']}] (データなし/基幹系統)")


# ──────────────────────────────────────────────
# 4. 周辺エリア空容量マップ (半径10km)
# ──────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# OSM変電所の座標マッピング
osm_sub_coords = {}
for feat in osm_subs["features"]:
    name = feat["properties"].get("name") or ""
    if "変電所" not in name:
        continue
    geom = shape(feat["geometry"])
    c = geom.centroid
    osm_sub_coords[name] = (c.y, c.x)

# 配電用変電所の座標を推定（OSM名マッチング）
nearby_subs = []
for row in dist_subs:
    sub_name = row["変電所名"]
    osm_key = f"{sub_name}変電所"
    if osm_key in osm_sub_coords:
        lat, lon = osm_sub_coords[osm_key]
        dist = haversine(tsuga_center[0], tsuga_center[1], lat, lon)
        if dist <= 10.0:
            row["_lat"] = lat
            row["_lon"] = lon
            row["_dist_km"] = round(dist, 2)
            nearby_subs.append(row)

# 都賀自身がリストに入っていなければ追加
if not any(r["No"] == "62" for r in nearby_subs):
    tsuga_dist["_lat"] = tsuga_center[0]
    tsuga_dist["_lon"] = tsuga_center[1]
    tsuga_dist["_dist_km"] = 0.0
    nearby_subs.append(tsuga_dist)

nearby_subs.sort(key=lambda r: r["_dist_km"])

print("\n=== 都賀変電所から10km以内の配電用変電所 ===")
print(f"{'No':>4s} {'変電所名':　<8s} {'距離km':>6s} {'設備MW':>6s} {'運用MW':>6s} {'空容量(設備)MW':>13s} {'空容量(上位系)MW':>15s} {'備考'}")
print("-" * 120)
for r in nearby_subs:
    print(f"{r['No']:>4s} {r['変電所名']:　<8s} {r['_dist_km']:6.1f} {r['設備容量MW']:>6s} {r['運用容量値MW']:>6s} "
          f"{r['空容量_当該設備MW']:>13s} {r['空容量_上位系等考慮MW']:>15s} {r['備考']}")


# ──────────────────────────────────────────────
# 5. Markdown レポート出力
# ──────────────────────────────────────────────
md_lines = []
md_lines.append("# 都賀変電所エリア詳細分析レポート\n")
md_lines.append(f"分析日: 2026-03-15\n")
md_lines.append("## 1. 案件概要\n")
md_lines.append("- **事業者**: P_X（蓄電所建売）")
md_lines.append("- **場所**: 栃木市都賀町家中")
md_lines.append("- **連系方式**: 高圧配電線連系")
md_lines.append("- **関連変電所**: 都賀変電所（配電用No.62）\n")

md_lines.append("## 2. 都賀変電所の基本情報\n")
md_lines.append("| 項目 | 値 |")
md_lines.append("|------|-----|")
md_lines.append(f"| 変電所名 | {tsuga_dist['変電所名']} |")
md_lines.append(f"| 電圧 | {tsuga_dist['電圧kV_一次']}/{tsuga_dist['電圧kV_二次']}kV |")
md_lines.append(f"| 台数 | {tsuga_dist['台数']} |")
md_lines.append(f"| 設備容量 | {tsuga_dist['設備容量MW']}MW |")
md_lines.append(f"| 運用容量 | {tsuga_dist['運用容量値MW']}MW |")
md_lines.append(f"| 運用容量制約要因 | {tsuga_dist['運用容量制約要因']} |")
md_lines.append(f"| 空容量（当該設備） | **{tsuga_dist['空容量_当該設備MW']}MW** |")
md_lines.append(f"| 空容量（上位系考慮） | **{tsuga_dist['空容量_上位系等考慮MW']}MW** |")
md_lines.append(f"| N-1電制 | {tsuga_dist['N1電制_適用可否']} |")
md_lines.append(f"| 平常時出力制御 | {tsuga_dist['平常時出力制御の可能性']} |")
md_lines.append(f"| 位置（推定） | {tsuga_center[0]:.5f}, {tsuga_center[1]:.5f} |")
md_lines.append(f"| 備考 | {tsuga_dist['備考']} |\n")

md_lines.append("## 3. 上位系統の制約チェーン分析\n")
md_lines.append("都賀変電所から上位系統に向けて、どの設備がボトルネックとなっているかを追跡した。\n")
md_lines.append("```")
md_lines.append(f"都賀変電所(配電用No.62) — 空容量(設備)=4MW / 空容量(上位系考慮)=0MW")
for i, item in enumerate(constraint_chain):
    indent = "  " * (i + 1)
    arrow = "└→ "
    if item["data"]:
        d = item["data"]
        cap_own = d.get("空容量_当該設備MW", "-")
        cap_upper = d.get("空容量_上位系等考慮MW", "-")
        name_key = "送電線名" if item["type"] == "送電線" else "変電所名"
        name = d.get(name_key, item["name"])
        md_lines.append(f"{indent}{arrow}{item['type']} {item['voltage']}kV No.{item['no']} [{name}] — 空容量(設備)={cap_own}MW / 空容量(上位系考慮)={cap_upper}MW")
    else:
        md_lines.append(f"{indent}{arrow}{item['type']} [{item['name']}] — 基幹系統（データなし）")
md_lines.append("```\n")

# ボトルネック特定
md_lines.append("### ボトルネック分析\n")
md_lines.append("制約チェーンの各段階で空容量(上位系考慮)が0MWとなっており、")
md_lines.append("最終的には**基幹系統（変3: 500/275kVバンク）**がボトルネックとなっている。\n")
md_lines.append("具体的な制約の流れ:\n")
md_lines.append("1. **都賀変電所** (配電用): 設備上は4MW空きがあるが、上位系制約で0MW")
md_lines.append("2. **小倉川線** (66kV送電線No.36): 設備上も0MW、上位の玉生線に制約")
md_lines.append("3. **玉生線** (66kV送電線No.31): 設備上も0MW、上位の西宇都宮変電所に制約")
md_lines.append("4. **西宇都宮変電所** (66kV変電所No.4): 設備上0MW、154kV系統に制約")
md_lines.append("5. 最終的に**基幹系統バンク**（変3）に到達\n")
md_lines.append("> **結論**: 都賀変電所エリアは多段階の系統制約が存在し、")
md_lines.append("> 高圧配電線連系であっても上位系制約により空容量は実質0MW。")
md_lines.append("> 蓄電所の連系には**出力制御**が前提となる可能性が高い。\n")

md_lines.append("## 4. 周辺配電用変電所の空容量比較（半径10km以内）\n")
md_lines.append("| No | 変電所名 | 距離(km) | 設備容量(MW) | 運用容量(MW) | 空容量-設備(MW) | 空容量-上位系(MW) | 備考 |")
md_lines.append("|:--:|:--------:|:--------:|:----------:|:----------:|:-------------:|:--------------:|:-----|")
for r in nearby_subs:
    marker = " **★**" if r["No"] == "62" else ""
    md_lines.append(f"| {r['No']} | {r['変電所名']}{marker} | {r['_dist_km']:.1f} | {r['設備容量MW']} | {r['運用容量値MW']} | {r['空容量_当該設備MW']} | {r['空容量_上位系等考慮MW']} | {r['備考']} |")
md_lines.append("")

# 代替連系先
md_lines.append("### 代替連系先の候補\n")
alternatives = [r for r in nearby_subs if r["No"] != "62" and float(r["空容量_上位系等考慮MW"]) > 0]
if alternatives:
    for r in alternatives:
        md_lines.append(f"- **{r['変電所名']}変電所** (No.{r['No']}): 上位系考慮後の空容量 {r['空容量_上位系等考慮MW']}MW、距離 {r['_dist_km']}km")
else:
    md_lines.append("半径10km以内に**上位系考慮後の空容量がある配電用変電所は存在しない**。")
    md_lines.append("これは栃木県南部エリア全体が基幹系統（変3）の制約を受けているためである。\n")
    # 設備空容量のある変電所
    alt_device = [r for r in nearby_subs if r["No"] != "62" and float(r["空容量_当該設備MW"]) > 0]
    if alt_device:
        md_lines.append("なお、設備空容量（上位系制約を考慮しない）が残っている変電所は以下の通り:")
        for r in alt_device:
            md_lines.append(f"- {r['変電所名']}変電所 (No.{r['No']}): 設備空容量 {r['空容量_当該設備MW']}MW（ただし上位系制約で実質0MW）")

md_lines.append("\n## 5. 蓄電所案件への示唆\n")
md_lines.append("### 高圧配電線連系のポイント\n")
md_lines.append("- 高圧配電線連系（6.6kV以下）は配電用変電所の空容量が影響")
md_lines.append("- 都賀変電所は設備上4MWの空きがあるが、上位系制約で実質0MW")
md_lines.append("- **蓄電所（充放電設備）の場合**、逆潮流（放電時の売電）が系統制約に抵触する可能性\n")
md_lines.append("### 出力制御リスク\n")
md_lines.append("- 平常時出力制御の可能性: **有り**")
md_lines.append("- 上位系設備の出力制御: 変3（基幹系統）")
md_lines.append("- N-1電制: **不可**（都賀変電所単体では適用不可）\n")
md_lines.append("### 推奨事項\n")
md_lines.append("1. 東京電力PGとの事前協議で、蓄電所の充放電パターンと系統制約の詳細確認が必要")
md_lines.append("2. 系統増強の予定（小倉川線・玉生線は「増強予定」の記載あり）を確認し、増強後の空容量を確認")
md_lines.append("3. 出力制御を前提とした事業計画の策定が必要")
md_lines.append("4. 栃木県南部エリア全体が制約下にあるため、近隣での代替連系先は限定的\n")

md_text = "\n".join(md_lines)
(OUTPUT / "tsuga_analysis.md").write_text(md_text, encoding="utf-8")
print(f"\nレポート出力: {OUTPUT / 'tsuga_analysis.md'}")


# ──────────────────────────────────────────────
# 6. Folium 地図出力
# ──────────────────────────────────────────────
m = folium.Map(location=tsuga_center, zoom_start=13, tiles="cartodbpositron")

# レイヤーグループ
fg_dist = folium.FeatureGroup(name="配電用変電所", show=True)
fg_upper = folium.FeatureGroup(name="上位変電所(OSM)", show=True)
fg_lines = folium.FeatureGroup(name="送電線(OSM)", show=True)
fg_plants = folium.FeatureGroup(name="発電所(OSM)", show=True)
fg_chain = folium.FeatureGroup(name="制約チェーン", show=True)

# --- 都賀変電所（中心）---
folium.Marker(
    location=tsuga_center,
    popup=folium.Popup(
        f"<b>都賀変電所</b> (配電用No.62)<br>"
        f"66/6.6kV以下<br>"
        f"設備容量: {tsuga_dist['設備容量MW']}MW<br>"
        f"空容量(設備): {tsuga_dist['空容量_当該設備MW']}MW<br>"
        f"空容量(上位系): {tsuga_dist['空容量_上位系等考慮MW']}MW<br>"
        f"<b style='color:red'>上位系制約で実質0MW</b>",
        max_width=300
    ),
    icon=folium.Icon(color="red", icon="star", prefix="fa"),
    tooltip="★ 都賀変電所（対象）"
).add_to(fg_dist)

# 10kmサークル
folium.Circle(
    location=tsuga_center,
    radius=10000,
    color="red",
    fill=False,
    weight=1,
    dash_array="5,10",
    tooltip="半径10km"
).add_to(fg_dist)

# --- 周辺配電用変電所 ---
for r in nearby_subs:
    if r["No"] == "62":
        continue
    cap_upper = float(r["空容量_上位系等考慮MW"])
    cap_own = float(r["空容量_当該設備MW"])
    if cap_upper > 0:
        color = "green"
    elif cap_own > 0:
        color = "orange"
    else:
        color = "gray"

    folium.CircleMarker(
        location=(r["_lat"], r["_lon"]),
        radius=8,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.7,
        popup=folium.Popup(
            f"<b>{r['変電所名']}変電所</b> (No.{r['No']})<br>"
            f"距離: {r['_dist_km']}km<br>"
            f"設備容量: {r['設備容量MW']}MW<br>"
            f"空容量(設備): {r['空容量_当該設備MW']}MW<br>"
            f"空容量(上位系): {r['空容量_上位系等考慮MW']}MW<br>"
            f"備考: {r['備考']}",
            max_width=300
        ),
        tooltip=f"{r['変電所名']} ({r['_dist_km']}km)"
    ).add_to(fg_dist)

# --- 上位変電所 (OSM) --- 半径20km以内
key_subs_names = ["西宇都宮変電所", "新栃木変電所", "壬生変電所", "栃木変電所", "小倉山下変電所"]
for feat in osm_subs["features"]:
    name = feat["properties"].get("name") or ""
    if "変電所" not in name:
        continue
    geom = shape(feat["geometry"])
    c = geom.centroid
    dist = haversine(tsuga_center[0], tsuga_center[1], c.y, c.x)
    if dist > 20:
        continue
    voltage_kv = feat["properties"].get("voltage_kv") or 0
    sub_type = feat["properties"].get("substation", "")
    if voltage_kv >= 66 or name in key_subs_names:
        color = "darkblue" if voltage_kv >= 154 else "blue"
        folium.Marker(
            location=(c.y, c.x),
            popup=folium.Popup(
                f"<b>{name}</b><br>"
                f"電圧: {voltage_kv}kV<br>"
                f"種別: {sub_type}<br>"
                f"距離: {dist:.1f}km",
                max_width=250
            ),
            icon=folium.Icon(color=color, icon="bolt", prefix="fa"),
            tooltip=f"{name} ({voltage_kv}kV)"
        ).add_to(fg_upper)

# --- 送電線 (OSM) --- 半径15km以内に一部でもかかるもの
highlighted_lines = ["小倉川線", "下都賀線", "玉生線"]
for feat in osm_lines["features"]:
    geom_data = feat["geometry"]
    name = feat["properties"].get("name") or ""
    voltage = feat["properties"].get("voltage") or ""

    # 距離フィルタ: ラインの座標のいずれかが15km以内
    coords = geom_data.get("coordinates", [])
    if geom_data["type"] == "LineString":
        in_range = any(haversine(tsuga_center[0], tsuga_center[1], c[1], c[0]) < 15 for c in coords)
    else:
        continue

    if not in_range:
        continue

    try:
        v_kv = int(voltage) // 1000 if voltage else 0
    except (ValueError, TypeError):
        v_kv = 0

    is_highlight = any(h in name for h in highlighted_lines) if name else False
    line_color = "red" if is_highlight else ("orange" if v_kv >= 154 else ("blue" if v_kv >= 66 else "gray"))
    line_weight = 4 if is_highlight else 2
    opacity = 0.9 if is_highlight else 0.5

    line_coords = [(c[1], c[0]) for c in coords]
    folium.PolyLine(
        locations=line_coords,
        color=line_color,
        weight=line_weight,
        opacity=opacity,
        tooltip=f"{name} ({v_kv}kV)" if name else f"送電線 ({v_kv}kV)",
        popup=f"<b>{name}</b><br>電圧: {v_kv}kV" if name else None
    ).add_to(fg_chain if is_highlight else fg_lines)

# --- 発電所 (OSM) --- 半径15km以内
for feat in osm_plants["features"]:
    geom = shape(feat["geometry"])
    c = geom.centroid
    dist = haversine(tsuga_center[0], tsuga_center[1], c.y, c.x)
    if dist > 15:
        continue
    name = feat["properties"].get("name") or feat["properties"].get("_display_name") or "発電所"
    source = feat["properties"].get("plant:source") or feat["properties"].get("fuel_type") or ""
    cap = feat["properties"].get("capacity_mw") or ""

    source_icons = {"solar": "sun-o", "hydro": "tint", "wind": "leaf", "biomass": "tree", "gas": "fire"}
    source_colors = {"solar": "orange", "hydro": "blue", "wind": "green", "biomass": "darkgreen", "gas": "red"}
    icon_name = source_icons.get(source, "industry")
    icon_color = source_colors.get(source, "gray")

    folium.CircleMarker(
        location=(c.y, c.x),
        radius=5,
        color=icon_color,
        fill=True,
        fill_color=icon_color,
        fill_opacity=0.6,
        tooltip=f"{name} ({source})" if source else name,
        popup=f"<b>{name}</b><br>種別: {source}<br>容量: {cap}MW" if cap else f"<b>{name}</b><br>種別: {source}"
    ).add_to(fg_plants)

# --- 制約チェーンの接続ライン ---
chain_coords = [tsuga_center]
chain_labels = ["都賀変電所"]

# 制約チェーンの各変電所の座標を取得
chain_sub_names = {
    "小倉川線": None,  # 送電線なので別扱い
    "玉生線": None,
    "西宇都宮": "西宇都宮変電所",
}

# 西宇都宮変電所の座標
if "西宇都宮変電所" in osm_sub_coords:
    nishi_utsunomiya = osm_sub_coords["西宇都宮変電所"]
    chain_coords.append(nishi_utsunomiya)
    chain_labels.append("西宇都宮変電所")

# 壬生変電所の座標（近くにある）
if "壬生変電所" in osm_sub_coords:
    mibu = osm_sub_coords["壬生変電所"]

# 制約チェーンの概念図（点線で接続）
if len(chain_coords) > 1:
    folium.PolyLine(
        locations=chain_coords,
        color="darkred",
        weight=3,
        dash_array="10,5",
        opacity=0.8,
        tooltip="制約チェーン: 都賀 → 西宇都宮"
    ).add_to(fg_chain)

# --- 都賀町家中の位置（蓄電所予定地） ---
# 都賀町家中は都賀変電所の近くと推定
ienaka_approx = (36.433, 139.755)  # 都賀町家中の概略位置
folium.Marker(
    location=ienaka_approx,
    popup="<b>蓄電所建設予定地</b><br>栃木市都賀町家中<br>(P_X 蓄電所建売)<br>高圧配電線連系予定",
    icon=folium.Icon(color="green", icon="battery-full", prefix="fa"),
    tooltip="★ 蓄電所予定地（都賀町家中）"
).add_to(fg_chain)

# 凡例を HTML で追加
legend_html = """
<div style="position: fixed; bottom: 20px; left: 20px; z-index: 1000;
     background-color: white; padding: 12px; border: 2px solid gray;
     border-radius: 5px; font-size: 12px; line-height: 1.6;">
<b>凡例</b><br>
<i class="fa fa-star" style="color:red"></i> 都賀変電所（対象）<br>
<i class="fa fa-battery-full" style="color:green"></i> 蓄電所予定地<br>
<span style="color:green">●</span> 空容量あり（上位系考慮）<br>
<span style="color:orange">●</span> 設備空容量のみ<br>
<span style="color:gray">●</span> 空容量なし<br>
<i class="fa fa-bolt" style="color:darkblue"></i> 上位変電所(154kV+)<br>
<i class="fa fa-bolt" style="color:blue"></i> 上位変電所(66kV)<br>
<span style="color:red">━━</span> 制約チェーン送電線<br>
<span style="color:darkred">- - -</span> 制約接続ライン<br>
<span style="color:blue">━</span> 送電線(66kV)<br>
<span style="color:orange">━</span> 送電線(154kV)<br>
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# タイトル
title_html = """
<div style="position: fixed; top: 10px; left: 60px; z-index: 1000;
     background-color: white; padding: 8px 15px; border: 2px solid #333;
     border-radius: 5px; font-size: 16px; font-weight: bold;">
都賀変電所エリア詳細マップ — P_X蓄電所建売案件分析
</div>
"""
m.get_root().html.add_child(folium.Element(title_html))

# レイヤー追加
fg_chain.add_to(m)
fg_dist.add_to(m)
fg_upper.add_to(m)
fg_lines.add_to(m)
fg_plants.add_to(m)
folium.LayerControl().add_to(m)

map_path = OUTPUT / "tsuga_detail_map.html"
m.save(str(map_path))
print(f"地図出力: {map_path}")

print("\n=== 分析完了 ===")
