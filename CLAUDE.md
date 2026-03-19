# CLAUDE.md

## プロジェクト概要

関東圏（栃木・千葉・茨城）の再エネ適地評価ツール。GIS-MCDA(AHP)手法でラスタースコア(5m/10m/30m)
およびベクターメッシュ(250m/500m/1km)の適地スコアを算出。GitHub Pages でインタラクティブマップを公開。

## 計算解像度

- **ラスター (raster_score.py)**: 5m / 10m / 30m (`--resolution` で指定)
  - 5m: 計算サーバー推奨 (1県あたり ~500MP、メモリ大)
  - 30m: デフォルト、ローカルPC可
- **ベクター (mesh_suitability.py)**: 250m / 500m / 1km

## 技術スタック

- Python: geopandas, shapely, folium, rasterio, pandapower, numpy, pandas
- フロントエンド: Leaflet.js (index.html は pure JS、Folium不使用)
- データ: All-Japan-Grid (OSM), 国土数値情報, SRTM DEM, Overpass API
- venv: `/tmp/pdfenv/bin/python3` (開発時)

## ディレクトリ構成

- `src/` — Pythonスクリプト (メッシュ計算、マップ生成、分析)
- `data/grid/` — 空容量CSV、変電所・送電線リスト (git管理)
- `data/land/` — GISデータ (gitignore、`download_land_data.py`で取得)
- `data/potential/` — REPOSポテンシャルCSV
- `output/` — 分析レポート (Markdown)、生成マップ (gitignore)
- `docs/` — GitHub Pages (index.html + GeoJSON + 画像)

## 開発ルール

- PowerXは全て `P_X` と表記する
- 変電所名は `name` カラムを使う (`_display_name` は使わない、23%しか名前がない)
- メッシュBBOX: `XMIN=139.32, YMIN=36.19, XMAX=140.30, YMAX=37.16` (栃木県実範囲)
- 土地利用コードは×10スケール (70=建物用地, 50=森林, 10=田, 60=荒地)
- 大規模モード: 建物用地=0(不適), 荒地=90(最適)
- ルーフトップモード: 建物用地=85(最適), 荒地=10(低)

## AHP重み

送電線距離25%, 傾斜20%, 変電所距離15%, 土地利用15%, 標高10%, 道路10%, 保護区域5%

## 主要な設計判断

- docs/index.html は Folium ではなく pure Leaflet.js (動的GeoJSONロード、軽量)
- メッシュGeoJSONはオンデマンドfetch (1km→500m→250m)
- ハザードマップは国土交通省タイルサーバーを直接参照
- 変電所はPolygon形式 (座標取得は getBounds().getCenter())

## 今後の方針

→ GitHub Issues に詳細あり
