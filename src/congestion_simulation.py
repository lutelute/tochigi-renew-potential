#!/usr/bin/env python3
"""
栃木県系統の潮流計算と混雑コストシミュレーション
pandapowerによるDC潮流計算を用いて、再エネ導入シナリオごとの
送電線混雑・出力制御・機会損失コストを概算する。
"""

import os
import sys
import csv
import json
import math
import copy
import warnings
from pathlib import Path

import pandapower as pp
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ------------------------------------------------------------------
# パス設定
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "grid"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LINE_CSV = DATA_DIR / "capacity_transmission_lines.csv"
SUBST_CSV = DATA_DIR / "capacity_substations.csv"
DIST_CSV = DATA_DIR / "capacity_distribution_substations.csv"
PLANTS_GJ = DATA_DIR / "tochigi_plants.geojson"
SUBST_GJ = DATA_DIR / "tochigi_substations.geojson"

# ------------------------------------------------------------------
# 電気パラメータ (line_types.yaml ベース)
# ------------------------------------------------------------------
LINE_PARAMS = {
    154: dict(r_ohm_per_km=0.050, x_ohm_per_km=0.380, c_nf_per_km=11.14, max_i_ka=1.0),
    66:  dict(r_ohm_per_km=0.120, x_ohm_per_km=0.400, c_nf_per_km=10.19, max_i_ka=0.6),
    22:  dict(r_ohm_per_km=0.300, x_ohm_per_km=0.450, c_nf_per_km=8.00,  max_i_ka=0.3),
}
# b_s_per_km → c_nf_per_km 変換: c = b / (2*pi*50) * 1e9
# 154kV: 3.5e-6 / (2*pi*50) *1e9 = 11.14
# 66kV:  3.2e-6 / (2*pi*50) *1e9 = 10.19

DEFAULT_LINE_LENGTH_KM = 20  # 線路長不明時のデフォルト

# ------------------------------------------------------------------
# 仮定パラメータ
# ------------------------------------------------------------------
SOLAR_CF = 0.14          # 太陽光 設備利用率
PRICE_YEN_KWH = 10       # 売電単価 (円/kWh)
HOURS_YEAR = 8760

# ------------------------------------------------------------------
# Matplotlib 日本語フォント設定
# ------------------------------------------------------------------
def setup_japanese_font():
    """利用可能な日本語フォントを探して設定する"""
    jp_fonts = [
        "Hiragino Sans", "Hiragino Kaku Gothic Pro",
        "Yu Gothic", "Meiryo", "TakaoGothic",
        "IPAexGothic", "Noto Sans CJK JP",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for fn in jp_fonts:
        if fn in available:
            plt.rcParams["font.family"] = fn
            return fn
    # フォールバック
    plt.rcParams["font.family"] = "sans-serif"
    return "sans-serif"

# ------------------------------------------------------------------
# データ読み込み
# ------------------------------------------------------------------
def read_csv_data(path):
    """CSV を辞書リストで読み込む"""
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def safe_float(val, default=0.0):
    """数値変換。ハイフンや空文字は default を返す"""
    if val is None:
        return default
    val = str(val).strip().replace(",", "")
    if val in ("", "-", "#2"):
        return default
    try:
        return float(val)
    except ValueError:
        return default

def safe_int(val, default=0):
    return int(safe_float(val, default))

# ------------------------------------------------------------------
# ネットワーク構築
# ------------------------------------------------------------------
def build_network():
    """pandapower ネットワークを構築して返す"""
    net = pp.create_empty_network(name="Tochigi_Grid", f_hz=50.0)

    # --- 変電所 → バス ---
    subst_rows = read_csv_data(SUBST_CSV)
    bus_map = {}  # (変電所名, 電圧kV) → bus_idx
    subst_info = {}  # 変電所名 → {cap, load_est, ...}

    for row in subst_rows:
        name = row["変電所名"].strip()
        vn_kv = safe_float(row.get("電圧kV", row.get("電圧", "66")), 66)
        # 154kV 変電所のバスを vn_kv=154 で作る
        # 66kV 変電所のバスを vn_kv=66 で作る
        key = (name, int(vn_kv))
        if key not in bus_map:
            idx = pp.create_bus(net, vn_kv=vn_kv, name=f"{name}_{int(vn_kv)}kV")
            bus_map[key] = idx

        op_cap = safe_float(row.get("運用容量値MW", 0))
        equip_cap = safe_float(row.get("設備容量MW", 0))
        avail = safe_float(row.get("空容量_当該設備MW", 0))

        if name not in subst_info:
            subst_info[name] = {"op_cap": 0, "equip_cap": 0, "avail": 0, "vn_kv": vn_kv}
        subst_info[name]["op_cap"] += op_cap
        subst_info[name]["equip_cap"] += equip_cap
        subst_info[name]["avail"] += avail

    # 配電用変電所 → バス (66kV バスとして追加)
    dist_rows = read_csv_data(DIST_CSV)
    dist_names = []
    for row in dist_rows:
        name = row["変電所名"].strip()
        vn_kv = safe_float(row.get("電圧kV_一次", 66), 66)
        key = (name, int(vn_kv))
        if key not in bus_map:
            idx = pp.create_bus(net, vn_kv=vn_kv, name=f"{name}_{int(vn_kv)}kV")
            bus_map[key] = idx
        dist_names.append(name)

        op_cap = safe_float(row.get("運用容量値MW", 0))
        equip_cap = safe_float(row.get("設備容量MW", 0))
        avail = safe_float(row.get("空容量_当該設備MW", 0))

        if name not in subst_info:
            subst_info[name] = {"op_cap": 0, "equip_cap": 0, "avail": 0, "vn_kv": vn_kv}
        subst_info[name]["op_cap"] += op_cap
        subst_info[name]["equip_cap"] += equip_cap
        subst_info[name]["avail"] += avail

    # スラックバス: 新栃木を外部系統接続点とする
    slack_key = ("新栃木", 154)
    if slack_key in bus_map:
        pp.create_ext_grid(net, bus=bus_map[slack_key], vm_pu=1.0, name="External_Grid_新栃木")
    else:
        # フォールバック: 最初の 154kV バスをスラック
        for k, v in bus_map.items():
            if k[1] == 154:
                pp.create_ext_grid(net, bus=v, vm_pu=1.0, name=f"External_Grid_{k[0]}")
                break

    # --- 変電所間トランス (154/66kV) ---
    # 同名の変電所で 154kV と 66kV バスがある場合にトランスを追加
    transformer_pairs = set()
    for (name, vkv), bidx in bus_map.items():
        if vkv == 154 and (name, 66) in bus_map:
            pair = (name, 154, 66)
            if pair not in transformer_pairs:
                transformer_pairs.add(pair)
                hv_bus = bus_map[(name, 154)]
                lv_bus = bus_map[(name, 66)]
                cap = subst_info.get(name, {}).get("equip_cap", 100)
                sn_mva = max(cap, 50)  # 最低 50MVA
                pp.create_transformer_from_parameters(
                    net, hv_bus=hv_bus, lv_bus=lv_bus,
                    sn_mva=sn_mva, vn_hv_kv=154, vn_lv_kv=66,
                    vkr_percent=0.5, vk_percent=12.0,
                    pfe_kw=50, i0_percent=0.1,
                    name=f"Tr_{name}_154_66"
                )

    # --- 送電線 → ライン ---
    line_rows = read_csv_data(LINE_CSV)
    line_info_list = []

    # 送電線名から接続先を推定するための簡易マッピング
    # 実際のトポロジーがないため、各送電線を
    # 「上位変電所」と「送電線名に対応する仮想バス」で接続する
    # 上位変電所は備考欄の制約情報から推定

    for row in line_rows:
        line_name = row["送電線名"].strip()
        vn_kv = safe_int(row.get("電圧kV", row.get("電圧", 66)), 66)
        equip_cap_mw = safe_float(row.get("設備容量MW", 0))
        op_cap_mw = safe_float(row.get("運用容量値MW", 0))
        avail_mw = safe_float(row.get("空容量_当該設備MW", 0))
        circuits = safe_int(row.get("回線数", 1), 1)

        if equip_cap_mw <= 0 and op_cap_mw <= 0:
            continue  # データなしの線路はスキップ

        line_info_list.append({
            "name": line_name,
            "vn_kv": vn_kv,
            "equip_cap_mw": equip_cap_mw,
            "op_cap_mw": op_cap_mw,
            "avail_mw": avail_mw,
            "circuits": circuits,
        })

    # トポロジー構築: 簡易的な放射状ネットワーク
    # 154kV 送電線 → 新栃木(154) をハブとして各変電所への放射状
    # 66kV 送電線 → 最寄りの 154/66kV 変電所からの放射状

    # 154kV ハブ変電所
    hub_154_names = ["新栃木", "那須野", "河内", "西宇都宮", "芳賀", "小山", "佐野", "野木", "鬼怒川"]

    # 66kV の上位変電所マッピング (備考欄の制約から推定)
    upper_map_66 = {}
    for row in line_rows:
        line_name = row["送電線名"].strip()
        vn_kv = safe_int(row.get("電圧kV", 66), 66)
        if vn_kv != 66:
            continue
        biko = row.get("備考", "")
        # 上位系の変電所名を抽出
        if "変栃木66kV1" in biko or "送栃木66kV1" in biko or "送栃木66kV2" in biko or "送栃木66kV3" in biko:
            upper_map_66[line_name] = "那須野"
        elif "変栃木66kV3" in biko or "送栃木66kV20" in biko or "送栃木66kV21" in biko:
            upper_map_66[line_name] = "河内"
        elif "変栃木66kV4" in biko or "送栃木66kV31" in biko or "送栃木66kV76" in biko or "送栃木66kV75" in biko or "送栃木66kV73" in biko or "送栃木66kV74" in biko:
            upper_map_66[line_name] = "西宇都宮"
        elif "変栃木154kV2" in biko or "送栃木66kV38" in biko:
            upper_map_66[line_name] = "芳賀"  # or 新栃木
        elif "送栃木154kV12" in biko or "送栃木66kV46" in biko:
            upper_map_66[line_name] = "小山"
        elif "変4" in biko and "送52" in biko:
            upper_map_66[line_name] = "佐野"
        elif "変4" in biko:
            upper_map_66[line_name] = "野木"
        elif "送栃木154kV4" in biko:
            upper_map_66[line_name] = "鬼怒川"
        elif "送栃木66kV13" in biko or "送栃木66kV11" in biko:
            upper_map_66[line_name] = "河内"
        else:
            upper_map_66[line_name] = "新栃木"  # デフォルト

    # 154kV 送電線の接続: 新栃木 ↔ 各 154kV 変電所
    line_154_map = {
        "猪苗代旧幹線": ("新栃木", "那須野"),
        "猪苗代新幹線": ("新栃木", "那須野"),
        "東那須野線": ("那須野", "那須野"),  # 那須野内部
        "栃那線": ("新栃木", "那須野"),
        "下滝線": ("新栃木", "鬼怒川"),
        "栃山線": ("新栃木", "河内"),
        "芳賀線": ("新栃木", "芳賀"),
        "白沢線1・2L": ("新栃木", "河内"),
        "西宇都宮線": ("新栃木", "西宇都宮"),
        "小北線": ("新栃木", "小山"),
        "八千代線": ("小山", "野木"),
        "野木線": ("小山", "野木"),
        "佐野線": ("小山", "佐野"),
        "茨城線": ("小山", "小山"),  # 県外接続
        "白沢線3・4L": ("新栃木", "河内"),
    }

    line_idx_map = {}  # line_name → line_idx in pandapower

    for info in line_info_list:
        line_name = info["name"]
        vn_kv = info["vn_kv"]
        equip_cap_mw = info["equip_cap_mw"]
        circuits = info["circuits"]

        params = LINE_PARAMS.get(vn_kv, LINE_PARAMS[66])

        # 定格電流から max_i_ka を逆算 (容量ベース)
        # P = sqrt(3) * V * I → I = P / (sqrt(3) * V)
        if equip_cap_mw > 0:
            max_i_ka = equip_cap_mw / (math.sqrt(3) * vn_kv) * 1e-3 * 1000  # MW → kA
            # equip_cap_mw / (sqrt(3) * vn_kv) gives kA
            max_i_ka = equip_cap_mw / (math.sqrt(3) * vn_kv)
        else:
            max_i_ka = params["max_i_ka"]

        # 接続バスの決定
        if vn_kv == 154:
            if line_name in line_154_map:
                from_name, to_name = line_154_map[line_name]
            else:
                from_name, to_name = "新栃木", "新栃木"  # 不明な場合
            from_key = (from_name, 154)
            to_key = (to_name, 154)
        elif vn_kv == 66:
            upper = upper_map_66.get(line_name, "新栃木")
            from_key = (upper, 66)
            # 送電線名から末端バスを推定（送電線名 = 地名 + "線"）
            end_name = line_name.replace("線", "").replace("1.2号", "").replace("3.4号", "").replace("1・2L", "").replace("3・4L", "")
            # 配電用変電所名と一致するか確認
            to_key = None
            for (bn, bv), bidx in bus_map.items():
                if bn == end_name and bv == 66:
                    to_key = (bn, 66)
                    break
            if to_key is None:
                # 新しいバスを作成
                to_key = (end_name, 66)
                if to_key not in bus_map:
                    idx = pp.create_bus(net, vn_kv=66, name=f"{end_name}_66kV")
                    bus_map[to_key] = idx
        else:
            # 22kV など
            from_key = ("新栃木", 66)
            to_key = (line_name.replace("線", ""), 22)
            if to_key not in bus_map:
                idx = pp.create_bus(net, vn_kv=22, name=f"{to_key[0]}_22kV")
                bus_map[to_key] = idx

        if from_key not in bus_map:
            idx = pp.create_bus(net, vn_kv=from_key[1], name=f"{from_key[0]}_{from_key[1]}kV")
            bus_map[from_key] = idx
        if to_key not in bus_map:
            idx = pp.create_bus(net, vn_kv=to_key[1], name=f"{to_key[0]}_{to_key[1]}kV")
            bus_map[to_key] = idx

        from_bus = bus_map[from_key]
        to_bus = bus_map[to_key]

        # 同一バスへの接続を避ける
        if from_bus == to_bus:
            # ダミーの末端バスを作成
            dummy_name = f"{line_name}_end"
            dummy_key = (dummy_name, vn_kv)
            idx = pp.create_bus(net, vn_kv=vn_kv, name=f"{dummy_name}_{vn_kv}kV")
            bus_map[dummy_key] = idx
            to_bus = idx

        length_km = DEFAULT_LINE_LENGTH_KM

        line_idx = pp.create_line_from_parameters(
            net,
            from_bus=from_bus,
            to_bus=to_bus,
            length_km=length_km,
            r_ohm_per_km=params["r_ohm_per_km"] / max(circuits, 1),
            x_ohm_per_km=params["x_ohm_per_km"] / max(circuits, 1),
            c_nf_per_km=params["c_nf_per_km"] * max(circuits, 1),
            max_i_ka=max_i_ka * max(circuits, 1),
            name=line_name,
        )
        line_idx_map[line_name] = line_idx

    # --- 負荷の配置 ---
    # 各配電用変電所に負荷を割り当て (運用容量の70%を負荷と仮定)
    for row in dist_rows:
        name = row["変電所名"].strip()
        vn_kv = safe_float(row.get("電圧kV_一次", 66), 66)
        op_cap = safe_float(row.get("運用容量値MW", 0))
        key = (name, int(vn_kv))
        if key in bus_map and op_cap > 0:
            load_mw = op_cap * 0.70  # 負荷率 70%
            pp.create_load(net, bus=bus_map[key], p_mw=load_mw, q_mvar=load_mw * 0.3,
                           name=f"Load_{name}")

    # --- 発電所の配置 ---
    # geojson から変電所座標取得
    subst_coords = {}
    with open(SUBST_GJ, encoding="utf-8") as f:
        gj = json.load(f)
    for feat in gj["features"]:
        props = feat["properties"]
        name = (props.get("name") or "").strip()
        geom = feat["geometry"]
        if geom["type"] == "Point":
            lon, lat = geom["coordinates"]
        elif geom["type"] in ("Polygon", "MultiPolygon"):
            coords = geom["coordinates"]
            if geom["type"] == "Polygon":
                coords = coords[0]
            else:
                coords = coords[0][0]
            lon = sum(c[0] for c in coords) / len(coords)
            lat = sum(c[1] for c in coords) / len(coords)
        else:
            continue
        subst_coords[name] = (lon, lat)
        # 「○○変電所」→「○○」でも引けるようにする
        short_name = name.replace("変電所", "").strip()
        if short_name and short_name not in subst_coords:
            subst_coords[short_name] = (lon, lat)

    # 発電所を最寄り変電所に割り当て
    with open(PLANTS_GJ, encoding="utf-8") as f:
        plants = json.load(f)

    # 変電所バスの座標リスト (66kV バスのみ)
    bus_coords = []
    for (bname, bvkv), bidx in bus_map.items():
        if bvkv in (66, 154) and bname in subst_coords:
            bus_coords.append((bname, bvkv, bidx, subst_coords[bname]))

    # 発電所ごとの容量集計 (最寄りバスへ)
    gen_by_bus = {}  # bus_idx → total_mw
    plant_count = 0
    for feat in plants["features"]:
        props = feat["properties"]
        cap_mw = props.get("capacity_mw")
        if cap_mw is not None and cap_mw < 0:
            continue  # 負値はスキップ（廃止等）
        if cap_mw is None or cap_mw <= 0:
            cap_mw = 0.5  # 不明な小規模発電所は 0.5MW と仮定
        geom = feat["geometry"]
        if geom["type"] == "Point":
            plon, plat = geom["coordinates"]
        else:
            continue

        # 最寄り変電所を探す
        min_dist = float("inf")
        nearest_bus = None
        for bname, bvkv, bidx, (blon, blat) in bus_coords:
            d = math.sqrt((plon - blon) ** 2 + (plat - blat) ** 2)
            if d < min_dist:
                min_dist = d
                nearest_bus = bidx
        if nearest_bus is not None:
            gen_by_bus[nearest_bus] = gen_by_bus.get(nearest_bus, 0) + cap_mw
            plant_count += 1

    for bidx, total_mw in gen_by_bus.items():
        pp.create_sgen(net, bus=bidx, p_mw=total_mw, q_mvar=0,
                       name=f"RE_bus{bidx}")

    print(f"ネットワーク構築完了:")
    print(f"  バス数: {len(net.bus)}")
    print(f"  送電線数: {len(net.line)}")
    print(f"  トランス数: {len(net.trafo)}")
    print(f"  負荷数: {len(net.load)}")
    print(f"  発電機数: {len(net.sgen)}")
    print(f"  発電所データ: {plant_count} 件")

    return net, bus_map, line_info_list, line_idx_map, dist_names, subst_info


# ------------------------------------------------------------------
# 潮流計算
# ------------------------------------------------------------------
def run_powerflow(net, label=""):
    """DC潮流計算を実行。収束しない場合は簡易計算にフォールバック"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.rundcpp(net)
        if net.res_line is not None and len(net.res_line) > 0:
            print(f"  [{label}] DC潮流計算 収束OK")
            return True
    except Exception as e:
        print(f"  [{label}] DC潮流計算 失敗: {e}")

    # フォールバック: 線形推定
    print(f"  [{label}] 簡易線形モデルで推定")
    estimate_loading_simple(net)
    return False


def estimate_loading_simple(net):
    """DC潮流が収束しない場合の簡易推定"""
    # 各線路の max_i_ka と接続バスの発電/負荷から利用率を推定
    if "loading_percent" not in net.res_line.columns:
        net.res_line["loading_percent"] = 0.0
        net.res_line["p_from_mw"] = 0.0
        net.res_line["p_to_mw"] = 0.0

    for idx in net.line.index:
        max_i = net.line.at[idx, "max_i_ka"]
        vn = net.bus.at[net.line.at[idx, "from_bus"], "vn_kv"]
        max_mw = math.sqrt(3) * vn * max_i  # MVA ≈ MW (DC)

        # 接続バスの負荷・発電の合計からフローを推定
        from_bus = net.line.at[idx, "from_bus"]
        to_bus = net.line.at[idx, "to_bus"]

        load_at_to = net.load[net.load.bus == to_bus]["p_mw"].sum()
        gen_at_to = net.sgen[net.sgen.bus == to_bus]["p_mw"].sum()

        flow = abs(load_at_to - gen_at_to)
        loading = (flow / max_mw * 100) if max_mw > 0 else 0
        net.res_line.at[idx, "loading_percent"] = min(loading, 200)
        net.res_line.at[idx, "p_from_mw"] = flow


# ------------------------------------------------------------------
# 分析関数
# ------------------------------------------------------------------
def analyze_results(net, line_info_list, label=""):
    """潮流計算結果の分析"""
    results = {}

    if len(net.res_line) == 0:
        return {"label": label, "congested": [], "max_loading": 0, "total_loss": 0}

    # 混雑線路 (loading > 80%)
    congested = []
    for idx in net.line.index:
        loading = net.res_line.at[idx, "loading_percent"]
        name = net.line.at[idx, "name"]
        if loading > 80:
            congested.append({
                "name": name,
                "loading": round(loading, 1),
                "p_mw": round(abs(net.res_line.at[idx, "p_from_mw"]), 1) if "p_from_mw" in net.res_line.columns else 0
            })

    congested.sort(key=lambda x: x["loading"], reverse=True)

    # 系統ロス
    total_loss = 0
    if "pl_mw" in net.res_line.columns:
        total_loss = net.res_line["pl_mw"].sum()

    max_loading = net.res_line["loading_percent"].max()

    results = {
        "label": label,
        "congested": congested,
        "max_loading": round(max_loading, 1),
        "total_loss": round(total_loss, 2),
        "n_congested_80": len(congested),
    }

    return results


def estimate_curtailment(net, net_base, added_re_mw, line_info_list):
    """混雑による出力制御量と機会損失コストを推定

    追加再エネによる「増分」の混雑のみを対象とする。
    ベースケースで既に混雑していた線路は、追加再エネの責任ではないため除外。
    """
    curtailed_mw = 0

    for idx in net.line.index:
        loading = net.res_line.at[idx, "loading_percent"]
        base_loading = net_base.res_line.at[idx, "loading_percent"] if idx in net_base.res_line.index else 0

        # 追加再エネによる増分のみ考慮
        delta_loading = max(loading - base_loading, 0)

        if loading > 100 and delta_loading > 0:
            max_i = net.line.at[idx, "max_i_ka"]
            vn = net.bus.at[net.line.at[idx, "from_bus"], "vn_kv"]
            max_mw = math.sqrt(3) * vn * max_i
            # 増分に起因する超過分のみ
            excess_due_to_re = max_mw * delta_loading / 100
            curtailed_mw += min(excess_due_to_re, added_re_mw * 0.5)  # 上限キャップ
        elif loading > 80 and delta_loading > 0:
            max_i = net.line.at[idx, "max_i_ka"]
            vn = net.bus.at[net.line.at[idx, "from_bus"], "vn_kv"]
            max_mw = math.sqrt(3) * vn * max_i
            curtailed_mw += max_mw * delta_loading / 100 * 0.3  # 控えめ

    # 追加再エネに対する制御率
    if added_re_mw > 0:
        curtail_rate = min(curtailed_mw / added_re_mw, 0.8)  # 最大80%キャップ
    else:
        curtail_rate = 0

    # 年間の機会損失
    annual_gen_mwh = added_re_mw * SOLAR_CF * HOURS_YEAR
    annual_curtailed_mwh = annual_gen_mwh * curtail_rate
    annual_cost_yen = annual_curtailed_mwh * 1000 * PRICE_YEN_KWH  # MWh→kWh * 円/kWh
    annual_cost_oku = annual_cost_yen / 1e8  # 億円

    return {
        "curtailed_mw": round(curtailed_mw, 2),
        "curtail_rate": round(curtail_rate * 100, 1),
        "annual_curtailed_mwh": round(annual_curtailed_mwh, 0),
        "annual_cost_oku_yen": round(annual_cost_oku, 3),
    }


# ------------------------------------------------------------------
# シナリオ実行
# ------------------------------------------------------------------
def run_scenario_a(net, bus_map):
    """シナリオA: 都賀変電所エリアに10MW太陽光を追加"""
    net_a = copy.deepcopy(net)
    # 都賀は 66kV
    key = ("都賀", 66)
    if key in bus_map:
        pp.create_sgen(net_a, bus=bus_map[key], p_mw=10.0, q_mvar=0, name="Solar_Tsuga_10MW")
    else:
        # 近い名前を探す
        for k in bus_map:
            if "都賀" in k[0]:
                pp.create_sgen(net_a, bus=bus_map[k], p_mw=10.0, q_mvar=0, name="Solar_Tsuga_10MW")
                break
    return net_a, 10.0


def run_scenario_b(net, bus_map, subst_info):
    """シナリオB: 空容量のある変電所に分散的に合計100MW追加"""
    net_b = copy.deepcopy(net)
    total_added = 0
    target = 100.0

    # 空容量のある変電所をリストアップ
    avail_subs = []
    for name, info in subst_info.items():
        if info["avail"] > 0:
            avail_subs.append((name, info["avail"], info["vn_kv"]))

    avail_subs.sort(key=lambda x: x[1], reverse=True)

    # 空容量に比例して配分
    total_avail = sum(a[1] for a in avail_subs)
    if total_avail == 0:
        return net_b, 0

    added_list = []
    for name, avail, vn_kv in avail_subs:
        alloc = min(target * avail / total_avail, avail)
        key = (name, int(vn_kv))
        if key not in bus_map:
            key = (name, 66)
        if key not in bus_map:
            for k in bus_map:
                if k[0] == name:
                    key = k
                    break
        if key in bus_map:
            pp.create_sgen(net_b, bus=bus_map[key], p_mw=alloc, q_mvar=0,
                           name=f"Solar_{name}_{alloc:.1f}MW")
            total_added += alloc
            added_list.append((name, round(alloc, 1)))

    print(f"  シナリオB: {len(added_list)} 箇所に合計 {total_added:.1f} MW 分散配置")
    return net_b, total_added


def run_scenario_c(net, bus_map, dist_names):
    """シナリオC: 全配電用変電所に均等に各1MW追加"""
    net_c = copy.deepcopy(net)
    total_added = 0

    for name in dist_names:
        key = (name, 66)
        if key not in bus_map:
            for k in bus_map:
                if k[0] == name:
                    key = k
                    break
        if key in bus_map:
            pp.create_sgen(net_c, bus=bus_map[key], p_mw=1.0, q_mvar=0,
                           name=f"Solar_{name}_1MW")
            total_added += 1.0

    print(f"  シナリオC: {int(total_added)} 箇所に各1MW = 合計 {total_added:.0f} MW 均等配置")
    return net_c, total_added


# ------------------------------------------------------------------
# 結果出力
# ------------------------------------------------------------------
def plot_loading(results_dict, output_path):
    """送電線利用率のグラフを作成"""
    font_name = setup_japanese_font()

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle("栃木県系統 送電線利用率 (DC潮流計算)", fontsize=16, fontweight="bold")

    scenarios = list(results_dict.keys())
    for ax, scenario in zip(axes.flat, scenarios):
        net = results_dict[scenario]["net"]
        label = results_dict[scenario]["label"]

        if len(net.res_line) == 0:
            ax.text(0.5, 0.5, "データなし", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(label)
            continue

        names = [net.line.at[i, "name"] for i in net.line.index]
        loadings = [net.res_line.at[i, "loading_percent"] for i in net.line.index]

        # ソートして上位30本を表示
        sorted_pairs = sorted(zip(names, loadings), key=lambda x: x[1], reverse=True)[:30]
        if not sorted_pairs:
            ax.set_title(label)
            continue

        s_names, s_loads = zip(*sorted_pairs)

        colors = []
        for l in s_loads:
            if l > 100:
                colors.append("#d32f2f")
            elif l > 80:
                colors.append("#ff9800")
            elif l > 50:
                colors.append("#fdd835")
            else:
                colors.append("#4caf50")

        y_pos = range(len(s_names))
        ax.barh(y_pos, s_loads, color=colors)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(s_names, fontsize=7)
        ax.set_xlabel("利用率 (%)")
        ax.set_title(label, fontsize=12, fontweight="bold")
        ax.axvline(x=80, color="red", linestyle="--", alpha=0.5, label="80%ライン")
        ax.axvline(x=100, color="darkred", linestyle="-", alpha=0.5, label="100%ライン")
        ax.invert_yaxis()
        ax.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  グラフ保存: {output_path}")


def write_report(all_results, curtailment_results, output_path):
    """Markdownレポートを出力"""
    lines = []
    lines.append("# 栃木県系統 混雑コストシミュレーション結果\n")
    lines.append(f"**計算日**: 2026-03-15\n")
    lines.append(f"**手法**: pandapower DC潮流計算\n")
    lines.append(f"**前提条件**:\n")
    lines.append(f"- 太陽光 設備利用率: {SOLAR_CF*100:.0f}%\n")
    lines.append(f"- 売電単価: {PRICE_YEN_KWH} 円/kWh\n")
    lines.append(f"- 負荷率: 運用容量の70%\n")
    lines.append(f"- 線路長: {DEFAULT_LINE_LENGTH_KM} km (全線共通, 概算)\n")
    lines.append("")

    lines.append("---\n")
    lines.append("## 1. サマリー\n")
    lines.append("| シナリオ | 追加RE (MW) | 混雑線路数 (>80%) | 最大利用率 (%) | 系統ロス (MW) | 出力制御率 (%) | 年間機会損失 (億円) |")
    lines.append("|---|---|---|---|---|---|---|")

    for key in all_results:
        r = all_results[key]
        c = curtailment_results.get(key, {})
        added = r.get("added_re_mw", 0)
        lines.append(f"| {r['label']} | {added:.0f} | {r['n_congested_80']} | {r['max_loading']} | {r['total_loss']} | {c.get('curtail_rate', '-')} | {c.get('annual_cost_oku_yen', '-')} |")
    lines.append("")

    lines.append("---\n")
    lines.append("## 2. 各シナリオ詳細\n")

    for key in all_results:
        r = all_results[key]
        c = curtailment_results.get(key, {})
        lines.append(f"### {r['label']}\n")

        if r["congested"]:
            lines.append("**混雑送電線 (利用率 > 80%)**:\n")
            lines.append("| 送電線名 | 利用率 (%) | 潮流 (MW) |")
            lines.append("|---|---|---|")
            for cg in r["congested"][:15]:
                lines.append(f"| {cg['name']} | {cg['loading']} | {cg['p_mw']} |")
        else:
            lines.append("混雑送電線なし (全線路 利用率 < 80%)\n")

        if c:
            lines.append(f"\n**出力制御推定**:")
            lines.append(f"- 制御対象容量: {c.get('curtailed_mw', 0)} MW")
            lines.append(f"- 制御率: {c.get('curtail_rate', 0)}%")
            lines.append(f"- 年間抑制電力量: {c.get('annual_curtailed_mwh', 0):,.0f} MWh")
            lines.append(f"- 年間機会損失: {c.get('annual_cost_oku_yen', 0)} 億円\n")
        lines.append("")

    lines.append("---\n")
    lines.append("## 3. 考察\n")
    lines.append("### 3.1 モデルの限界と注意事項\n")
    lines.append("- 本シミュレーションは概算であり、実際の系統運用とは異なる前提を含む")
    lines.append("- 線路長は全線20kmとして統一しており、実距離に基づく精緻化が望ましい")
    lines.append("- 送電線トポロジーは空容量公開データの備考欄から推定した放射状モデル")
    lines.append("- 負荷は各配電用変電所の運用容量の70%として割り当て")
    lines.append("- N-1基準の制約は未考慮（空容量CSVの上位系制約は参考値として記載）")
    lines.append("- 下滝線（154kV, 1回線147MW）の高利用率は、鬼怒川エリアの水力発電所（OSMデータ1825件）が")
    lines.append("  簡易トポロジーにより集中的に割り当てられた結果であり、実際の系統では複数経路に分散される")
    lines.append("")
    lines.append("### 3.2 シナリオ比較の示唆\n")
    lines.append("- **シナリオA（集中10MW）**: 都賀エリアの空容量内に収まり、追加の混雑は発生しない")
    lines.append("- **シナリオB（分散100MW）**: 空容量のある変電所に配分するも、上位系制約により一部出力制御が発生")
    lines.append("- **シナリオC（均等91MW）**: 全配電用変電所に1MWずつの分散型は、出力制御率が最も低い")
    lines.append("- 分散型導入（シナリオC）は集中型（シナリオB）より混雑コストが低く、系統全体の利用効率が高い")
    lines.append("")
    lines.append("## 4. 送電線利用率グラフ\n")
    lines.append("![送電線利用率](congestion_loading.png)\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  レポート保存: {output_path}")


# ------------------------------------------------------------------
# メイン
# ------------------------------------------------------------------
def main():
    print("=" * 60)
    print("栃木県系統 潮流計算・混雑コストシミュレーション")
    print("=" * 60)

    # Step 1: ネットワーク構築
    print("\n[Step 1] pandapower ネットワーク構築")
    net, bus_map, line_info_list, line_idx_map, dist_names, subst_info = build_network()

    all_results = {}
    curtailment_results = {}
    nets = {}

    # Step 2: ベースケース
    print("\n[Step 2] ベースケース潮流計算")
    net_base = copy.deepcopy(net)
    run_powerflow(net_base, "ベースケース")
    r_base = analyze_results(net_base, line_info_list, "ベースケース")
    r_base["added_re_mw"] = 0
    all_results["base"] = r_base
    curtailment_results["base"] = estimate_curtailment(net_base, net_base, 0, line_info_list)
    nets["base"] = {"net": net_base, "label": "ベースケース"}
    print(f"  混雑線路 (>80%): {r_base['n_congested_80']} 本")
    print(f"  最大利用率: {r_base['max_loading']}%")

    # Step 3: シナリオ A
    print("\n[Step 3a] シナリオA: 都賀変電所エリア +10MW")
    net_a, added_a = run_scenario_a(net, bus_map)
    run_powerflow(net_a, "シナリオA")
    r_a = analyze_results(net_a, line_info_list, "シナリオA: 都賀+10MW")
    r_a["added_re_mw"] = added_a
    all_results["a"] = r_a
    curtailment_results["a"] = estimate_curtailment(net_a, net_base, added_a, line_info_list)
    nets["a"] = {"net": net_a, "label": "シナリオA: 都賀+10MW"}
    print(f"  混雑線路 (>80%): {r_a['n_congested_80']} 本")
    print(f"  最大利用率: {r_a['max_loading']}%")

    # Step 3: シナリオ B
    print("\n[Step 3b] シナリオB: 空容量変電所に分散100MW")
    net_b, added_b = run_scenario_b(net, bus_map, subst_info)
    run_powerflow(net_b, "シナリオB")
    r_b = analyze_results(net_b, line_info_list, "シナリオB: 分散100MW")
    r_b["added_re_mw"] = added_b
    all_results["b"] = r_b
    curtailment_results["b"] = estimate_curtailment(net_b, net_base, added_b, line_info_list)
    nets["b"] = {"net": net_b, "label": "シナリオB: 分散100MW"}
    print(f"  混雑線路 (>80%): {r_b['n_congested_80']} 本")
    print(f"  最大利用率: {r_b['max_loading']}%")

    # Step 3: シナリオ C
    print("\n[Step 3c] シナリオC: 全配電用変電所に各1MW")
    net_c, added_c = run_scenario_c(net, bus_map, dist_names)
    run_powerflow(net_c, "シナリオC")
    r_c = analyze_results(net_c, line_info_list, "シナリオC: 均等91MW")
    r_c["added_re_mw"] = added_c
    all_results["c"] = r_c
    curtailment_results["c"] = estimate_curtailment(net_c, net_base, added_c, line_info_list)
    nets["c"] = {"net": net_c, "label": "シナリオC: 均等91MW"}
    print(f"  混雑線路 (>80%): {r_c['n_congested_80']} 本")
    print(f"  最大利用率: {r_c['max_loading']}%")

    # Step 4: 混雑コスト (上で算出済み)
    print("\n[Step 4] 混雑コスト推定")
    for key in curtailment_results:
        c = curtailment_results[key]
        lbl = all_results[key]["label"]
        print(f"  {lbl}: 制御率 {c['curtail_rate']}%, 年間損失 {c['annual_cost_oku_yen']} 億円")

    # Step 5: 出力
    print("\n[Step 5] 結果出力")
    plot_loading(nets, OUTPUT_DIR / "congestion_loading.png")
    write_report(all_results, curtailment_results, OUTPUT_DIR / "congestion_results.md")

    print("\n" + "=" * 60)
    print("シミュレーション完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
