"""
栃木県 再エネポテンシャル GeoJSON 生成スクリプト

環境省REPOS推計値に基づく市町村別ポテンシャルデータと
国土数値情報 行政区域データ(N03)を結合し、
コロプレスマップ用 GeoJSON を出力する。

出力: data/potential/tochigi_potential.geojson
"""

import pathlib
import sys

import geopandas as gpd
import pandas as pd

# ── パス設定 ──────────────────────────────────
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SHP_PATH = BASE_DIR / "data" / "land" / "admin_boundary" / "N03-20240101_09.shp"
CSV_PATH = BASE_DIR / "data" / "potential" / "repos_tochigi.csv"
OUT_PATH = BASE_DIR / "data" / "potential" / "tochigi_potential.geojson"


def main():
    # ── 1. 行政区域データ読み込み ──────────────────
    print(f"[1/5] 行政区域データを読み込み中: {SHP_PATH}")
    gdf = gpd.read_file(str(SHP_PATH), encoding="utf-8")
    print(f"      レコード数: {len(gdf)}, CRS: {gdf.crs}")

    # ── 2. 市町村単位でディゾルブ ─────────────────
    print("[2/5] 市町村単位でディゾルブ...")
    # N03_004 が市町村名
    # N03_003 が郡名 (町村の場合)
    # N03_007 が市町村コード
    gdf_muni = gdf.dissolve(by="N03_004", as_index=False)

    # 面積を計算 (平面直角座標系 第IX系 EPSG:6677)
    gdf_proj = gdf_muni.to_crs(epsg=6677)
    gdf_muni["area_km2"] = gdf_proj.geometry.area / 1e6

    # CRS を WGS84 に統一
    gdf_muni = gdf_muni.to_crs(epsg=4326)
    print(f"      市町村数: {len(gdf_muni)}")

    # ── 3. REPOS ポテンシャルCSV 読み込み ──────────
    print(f"[3/5] REPOSポテンシャルCSVを読み込み中: {CSV_PATH}")
    df_pot = pd.read_csv(str(CSV_PATH), encoding="utf-8")
    print(f"      レコード数: {len(df_pot)}")
    print(f"      カラム: {df_pot.columns.tolist()}")

    # ── 4. 結合 (市町村名ベース) ──────────────────
    print("[4/5] 市町村名で結合...")

    # 結合キーの正規化 (空白除去)
    gdf_muni["市町村名"] = gdf_muni["N03_004"].str.strip()
    df_pot["市町村名"] = df_pot["市町村名"].str.strip()

    merged = gdf_muni.merge(df_pot, on="市町村名", how="left")

    # 結合結果の確認
    matched = merged["太陽光_土地系_MW"].notna().sum()
    unmatched = merged["太陽光_土地系_MW"].isna().sum()
    print(f"      結合成功: {matched}, 未結合: {unmatched}")

    if unmatched > 0:
        missing = merged.loc[merged["太陽光_土地系_MW"].isna(), "市町村名"].tolist()
        print(f"      未結合の市町村: {missing}")

    # ── 合計ポテンシャルの算出 ────────────────────
    pot_cols = [
        "太陽光_土地系_MW",
        "太陽光_建物系_MW",
        "陸上風力_MW",
        "中小水力_MW",
        "バイオマス_MW",
    ]
    merged["再エネ合計_MW"] = merged[pot_cols].sum(axis=1)

    # 太陽光合計
    merged["太陽光合計_MW"] = (
        merged["太陽光_土地系_MW"].fillna(0) + merged["太陽光_建物系_MW"].fillna(0)
    )

    # ポテンシャル密度 (MW/km2)
    merged["ポテンシャル密度_MW_per_km2"] = merged["再エネ合計_MW"] / merged["area_km2"]

    # ── 5. GeoJSON 出力 ──────────────────────────
    print(f"[5/5] GeoJSON 出力中: {OUT_PATH}")

    # 出力カラムの選定
    out_cols = [
        "N03_001",  # 都道府県名
        "N03_003",  # 郡名
        "N03_004",  # 市町村名
        "N03_007",  # 市町村コード
        "area_km2",
        "太陽光_土地系_MW",
        "太陽光_建物系_MW",
        "太陽光合計_MW",
        "陸上風力_MW",
        "中小水力_MW",
        "バイオマス_MW",
        "再エネ合計_MW",
        "ポテンシャル密度_MW_per_km2",
        "出典",
        "geometry",
    ]

    out_gdf = merged[out_cols].copy()

    # カラム名を英語に変換 (GIS互換性向上)
    rename_map = {
        "N03_001": "pref_name",
        "N03_003": "county_name",
        "N03_004": "muni_name",
        "N03_007": "muni_code",
        "太陽光_土地系_MW": "solar_land_mw",
        "太陽光_建物系_MW": "solar_bldg_mw",
        "太陽光合計_MW": "solar_total_mw",
        "陸上風力_MW": "wind_land_mw",
        "中小水力_MW": "hydro_small_mw",
        "バイオマス_MW": "biomass_mw",
        "再エネ合計_MW": "renew_total_mw",
        "ポテンシャル密度_MW_per_km2": "potential_density",
        "出典": "source",
    }
    out_gdf = out_gdf.rename(columns=rename_map)

    # 数値を丸める
    for col in ["area_km2", "potential_density"]:
        out_gdf[col] = out_gdf[col].round(2)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_gdf.to_file(str(OUT_PATH), driver="GeoJSON", encoding="utf-8")

    print("\n=== 完了 ===")
    print(f"出力ファイル: {OUT_PATH}")
    print(f"ファイルサイズ: {OUT_PATH.stat().st_size / 1024:.1f} KB")

    # サマリー表示
    print("\n--- 栃木県 再エネポテンシャル サマリー ---")
    summary_cols = [
        "solar_land_mw", "solar_bldg_mw", "wind_land_mw",
        "hydro_small_mw", "biomass_mw", "renew_total_mw",
    ]
    summary_labels = [
        "太陽光(土地系)", "太陽光(建物系)", "陸上風力",
        "中小水力", "バイオマス", "再エネ合計",
    ]
    for label, col in zip(summary_labels, summary_cols):
        total = out_gdf[col].sum()
        print(f"  {label}: {total:,.1f} MW")

    print(f"\n--- ポテンシャル上位5市町村 (再エネ合計) ---")
    top5 = out_gdf.nlargest(5, "renew_total_mw")
    for _, row in top5.iterrows():
        print(f"  {row['muni_name']}: {row['renew_total_mw']:,.1f} MW "
              f"(密度: {row['potential_density']:.2f} MW/km2)")


if __name__ == "__main__":
    main()
