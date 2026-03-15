# tochigi-renew-potential

栃木県の再エネポテンシャル推定・適地評価・系統空容量統合ツール

## 概要

GISデータを統合し、栃木県における再生可能エネルギー（太陽光・風力・バイオマス等）の
ポテンシャル推定、適地評価、電力系統の空容量情報を組み合わせたインタラクティブマップを生成する。

## データソース

| レイヤー | データソース | 形式 |
|---|---|---|
| 送電線・変電所 | [All-Japan-Grid](https://github.com/lutelute/All-Japan-Grid) (OSM由来) | GeoJSON |
| 系統空容量 | 東京電力PG 空容量マッピング (2025/1/7時点) | CSV (PDF抽出) |
| 森林地域 | 国土数値情報 A13 | Shapefile |
| 農業地域 | 国土数値情報 A12 | Shapefile |
| 行政区域 | 国土数値情報 N03 | Shapefile |

### 今後追加予定

- 環境省REPOS 再エネポテンシャル
- 基盤地図情報DEM (傾斜解析)
- 自然公園・保安林等の規制区域
- NEDO日射量データ
- 筆ポリゴン (農地区画)

## ディレクトリ構成

```
tochigi-renew-potential/
├── data/
│   ├── grid/           # 電力系統データ (GeoJSON + 空容量CSV)
│   ├── land/           # 土地利用・規制データ (国土数値情報)
│   └── potential/      # 再エネポテンシャルデータ (REPOS等)
├── src/
│   ├── extract_tochigi_grid.py   # 系統データ抽出
│   ├── download_land_data.py     # 土地利用データDL
│   └── build_map.py              # マップ生成
├── notebooks/          # 分析用ノートブック
├── output/
│   └── tochigi_grid_map.html     # インタラクティブマップ
└── README.md
```

## 使い方

```bash
# 1. 系統データ抽出 (All-Japan-Grid → 栃木県)
python src/extract_tochigi_grid.py

# 2. 土地利用データダウンロード
python src/download_land_data.py

# 3. マップ生成
python src/build_map.py
# → output/tochigi_grid_map.html をブラウザで開く
```

## マップレイヤー

- **送電線**: 500kV/275kV/154kV/66kV (電圧別に色分け、ON/OFF切替可)
- **変電所**: OSMデータの変電所位置 (電圧別)
- **配電用変電所 空容量**: 空容量0MW=赤, 1-50MW=橙, 51-200MW=黄, 200+MW=緑
- **特高変電所 空容量**: 154kV/66kV変電所の空容量
- **発電所**: 太陽光/水力/風力等 (クラスタ表示)
- **森林地域**: 国有林・民有林 (緑)
- **農業地域**: 農振地域 (黄土色)
- **行政区域**: 市町村境界 (破線)

## 依存関係

```
geopandas, shapely, folium, pandas, branca
```

## 関連プロジェクト

- [All-Japan-Grid](https://github.com/lutelute/All-Japan-Grid) - 全国電力系統GISデータ
- [all-japan-traffic-grid](https://github.com/lutelute/all-japan-traffic-grid) - 交通ネットワーク

## 背景

INPEXとの共同研究（プロジェクト今井）において、配電レベルの系統混雑による
社会ロス（推定年間1,000億円規模）の解決に向けた、地域情報と配電ネットワークの
カップリングプラットフォームの基盤として開発。
