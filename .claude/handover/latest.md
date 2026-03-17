# 引き継ぎ: kanto-re-potential (2026-03-16 21:00)

## 1. プロジェクト概要

関東圏（栃木・千葉・茨城）の再エネ適地評価ツール。GIS-MCDA(AHP)手法でメッシュ適地スコアを算出し、ラスタータイルでGitHub Pagesに配信。

- **リポジトリ**: https://github.com/lutelute/kanto-re-potential
- **Pages**: https://lutelute.github.io/kanto-re-potential/index.html
- **ローカル**: `/Users/shigenoburyuto/Library/CloudStorage/OneDrive-個人用/書籍(L)/001_福井大学/2026年度/【共研】プロジェクト今井/tochigi-renew-potential`
  - ※ローカルフォルダ名は旧名のまま
- **venv**: `/tmp/pdfenv/bin/python3`
- **GDAL**: `gdal2tiles.py` (brew install gdal, v3.12.2)
- **tippecanoe**: v2.79.0 (brew, PMTilesに使用したが現在未使用)

## 2. 現在のステータス

### 完了済み
- [x] 3県の空容量PDF抽出（CSV化済み）
- [x] 3県のAll-Japan-Grid系統データ抽出
- [x] 3県のGISデータDL（行政区域、農業、DEM、森林）
  - ※土地利用100mメッシュGeoTIFFは千葉・茨城で未取得（国土数値情報URL問題）
- [x] 3県の傾斜解析（slope TIF生成済み）
- [x] 3県のメッシュ適地評価（250m）
- [x] **ラスタータイル化**: 250mメッシュ → RGBA GeoTIFF → gdal2tiles z7-14 → PNG tiles
  - docs/{pref}/tiles/{z}/{x}/{y}.png 配置済み（3県合計13,400タイル, 52MB）
- [x] index.html: ラスタータイル方式、3県同時デフォルト表示、県セレクタ
- [x] 変電所CircleMarker化、空容量マップ、ハザードマップ
- [x] 横長3ブロック凡例、背景非表示選択

### 未完了（次のタスク）
1. **30mメッシュ生成 + ラスタータイル化**
   - SRTM 30m DEMから直接30m解像度でスコアを計算
   - mesh_suitability.pyを30m対応に拡張（現在は250m最小）
   - 各スコア（total, slope, grid_dist, sub_dist, land_use, elevation）ごとにGeoTIFF + タイル生成
   - ユーザー発言: 「できるだけ小さい方がいい」「1mメッシュできたら感動」
2. **AHPスコア別ラスタータイル**
   - 現在はtotal_scoreのみタイル化
   - 表示モードセレクタ（傾斜/送電線距離/変電所距離/土地利用/標高）に対応するタイルセットを生成
   - index.htmlの表示モード切替でタイルURLを動的変更
3. **空容量マップの千葉・茨城対応**
   - capDistDataが栃木のみハードコード → 県別JSON or 動的生成

## 3. ラスタータイル生成パイプライン

現在のパイプライン（250mメッシュ → total_scoreのみ）:

```
1. mesh_suitability.py --prefecture {pref} --resolution 250
   → output/{pref}/{pref}_mesh_250m.geojson

2. Python: GeoJSON → GeoTIFF (docs/{pref}/mesh_score.tif)
   - centroid抽出、numpy grid化、rasterio書き出し

3. Python: score GeoTIFF → RGBA GeoTIFF (/tmp/{pref}_rgba.tif)
   - score→色変換: >=80→darkgreen, >=60→forestgreen, >=40→goldenrod, >=20→darkorange, else→crimson

4. gdal2tiles.py -z 7-14 -w none --xyz -r near /tmp/{pref}_rgba.tif docs/{pref}/tiles/
```

**30mへの拡張方針**:
- mesh_suitability.pyで30mグリッド生成は遅すぎる（数百万セル）
- 代わりにrasterioで直接30mラスタ演算:
  - slope: SRTM 30m DEMから直接計算（slope_analysis.pyで既に生成）
  - grid_dist: 送電線ジオメトリからrasterio.features.rasterize
  - sub_dist: 変電所centroidからscipy.ndimage.distance_transform
  - land_use: 100mメッシュTIFをリサンプル（未取得県はデフォルト値）
  - elevation: SRTM 30m DEM直接
  - total: 重み付き合成

## 4. ディレクトリ構成

```
kanto-re-potential/
  src/
    config.py, extract_grid.py, extract_capacity_pdf.py,
    mesh_suitability.py, download_land_data.py, slope_analysis.py,
    build_integrated_map.py, build_map.py, build_potential_layer.py,
    congestion_simulation.py, tsuga_analysis.py,
    extract_tochigi_grid.py (旧版、extract_grid.pyに置換済み)
  data/{tochigi,chiba,ibaraki}/
    grid/   (capacity CSV + GeoJSON)
    land/   (admin_boundary, agriculture, forest, dem, slope TIF)
  output/{tochigi,chiba,ibaraki}/
  docs/
    index.html
    {tochigi,chiba,ibaraki}/
      tiles/{z}/{x}/{y}.png   ← ラスタータイル（現在total_scoreのみ）
      mesh_1000m.geojson      ← GeoJSON（フォールバック用、残存）
      mesh_500m.geojson, mesh_250m.geojson
      subs_66kv.geojson, lines_66kv.geojson, boundary.geojson
      mesh.pmtiles            ← 未使用（Canvas描画問題で無効化）
      mesh_score.tif          ← 未コミット（中間ファイル）
```

## 5. 重要な設計判断・教訓

- **PMTiles(ベクタータイル)は断念**: protomaps-leafletのCanvas描画が全面を覆い、送電線・変電所のクリック/ホバーをブロック。pane設定やpointerEvents:noneでは解決不能
- **ラスタータイル方式が正解**: ハザードマップと同じL.tileLayerで、pane='mesh'(zIndex=300, pointerEvents=none)に配置。完全にオーバーレイと干渉しない
- GeoJSONの500m/250mは49-58MBで重い。gitには残っているが、ラスタータイルが本命
- voltage_kv==0の変電所も含める（`v < 66 and v != 0`）
- subsReady PromiseはclearAllLayers時にリセット必要（let宣言）

## 6. 既知の問題

- 土地利用100mメッシュGeoTIFF: 千葉・茨城で未取得（国土数値情報URL変更）→ デフォルト値70で代用中
- 空容量マップ(capDistData): 栃木のみハードコード、千葉・茨城は未対応
- AHP重み調整チェックボックス: ラスタータイル方式では無効（静的画像）
  - → スコア別タイルセットで代替する計画
- extract_tochigi_grid.py（旧版）がsrc/に残っている（extract_grid.pyが後継）
- docs内の大容量GeoJSON (500m/250m) がgitに残っている → ラスタータイル完全移行後に削除検討

## 7. 次のアクション

**ゴール: 30mメッシュ + AHPスコア別タイルセット**

1. 30m解像度のラスタ演算スクリプト作成（`src/raster_score.py`新規）
   - rasterioベース、mesh_suitability.pyのベクタ演算をラスタ演算に置換
   - 入力: slope TIF, 送電線GeoJSON, 変電所GeoJSON, DEM HGT
   - 出力: score_total.tif, score_slope.tif, score_grid_dist.tif, score_sub_dist.tif, score_elevation.tif
2. 各スコアTIFをRGBA変換 + gdal2tiles
   - タイルディレクトリ: docs/{pref}/tiles_total/, tiles_slope/, tiles_grid/ 等
3. index.htmlの表示モードセレクタでタイルURLを切替
4. commit & push
5. GeoJSON 500m/250mをgit管理から除外（ラスタータイルに完全移行）

## 8. コンテキスト・メモ

- ユーザー（重信先生）: 「最後までノンストップで」「計画立てたりteamの実行は任せます」→ 自律的に進めてOK
- 「できるだけ小さい方がいい」「1mメッシュできたら感動」→ 30m以下を目指す
- 「各種AHPスコア選択も変更するように計算お願いします」→ スコア別タイルセット必須
- 「ハザードマップはかなり細かいけどどうやって？」→ ラスタータイル方式の説明済み、理解OK
- 「重いデータはgithubにあげず、ラスターだけあげとくとかがいいかもね」→ GeoJSON削除、タイルのみ配信の方針
