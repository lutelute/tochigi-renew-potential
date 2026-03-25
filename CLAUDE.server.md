# CLAUDE.md — サーバー自律実行用

## ミッション

全国47都道府県の再エネポテンシャルを5m解像度で計算する。
`src/batch_all_japan.py` を使って全都道府県を順次処理する。

## 前提条件

1. このリポジトリが `~/projects/kanto-re-potential/` にcloneされている
2. `~/All-Japan-Grid-ref/` にAll-Japan-Gridリポジトリがcloneされている
3. Python 3.10+ と必要パッケージがインストール済み
4. 環境変数 `ALL_JAPAN_GRID_DIR` が設定されている

## 実行手順

### Step 1: 環境確認

```bash
cd ~/projects/kanto-re-potential
git checkout feature/all-japan
export ALL_JAPAN_GRID_DIR=~/All-Japan-Grid-ref/data
python3 -c "from src.config import PREFECTURES; print(f'{len(PREFECTURES)} prefectures loaded')"
```

### Step 2: 福井県でテスト (30m)

```bash
python3 src/batch_all_japan.py -p fukui --resolution 30
```

成功を確認してから全国実行に進む。

### Step 3: 全国実行 (5m)

```bash
python3 src/batch_all_japan.py --resolution 5 --resume 2>&1 | tee logs/batch_main.log
```

### エラー時の対処

- **Overpass API タイムアウト**: 自動リトライ (3回) 済み。それでも失敗したら次の県に進む。
  失敗した県は後から `--prefecture <name> --resume` で再実行可能。
- **メモリ不足**: 北海道は14振興局に分割済み。それでも足りなければ `--resolution 10` に下げる。
- **SRTM ダウンロード失敗**: プロキシ設定が必要な場合:
  ```bash
  export http_proxy=http://ufproxy.b.cii.u-fukui.ac.jp:8080
  export https_proxy=http://ufproxy.b.cii.u-fukui.ac.jp:8080
  ```
- **All-Japan-Grid ファイルがない電力エリア**: extract_gridステップが失敗するが、次の県に進む。

### 進捗確認

外部から進捗確認する方法:

```bash
# リアルタイム進捗
tail -f ~/projects/kanto-re-potential/data/batch_progress.txt

# チェックポイント確認
cat ~/projects/kanto-re-potential/data/batch_checkpoint.json | python3 -m json.tool

# 完了した県の数
cat ~/projects/kanto-re-potential/data/batch_checkpoint.json | python3 -c "
import json, sys
cp = json.load(sys.stdin)
done = sum(1 for v in cp.values() if v.get('status') == 'completed')
total = len(cp)
print(f'Completed: {done}/{total}')
for k, v in cp.items():
    status = v.get('status', v.get('failed_step', 'in_progress'))
    print(f'  {k}: {status}')
"

# ログファイル確認
ls -la ~/projects/kanto-re-potential/logs/
```

## 技術的注意

- **計算サーバースペック**: 256GB RAM, 160コア, 3.5TB disk
- 5m解像度で1県あたり数百メガピクセル → メモリ数十GB使用
- 全47都道府県(北海道14分割で60ユニット)を順次処理
- タイル生成はスキップ (サーバーでは不要、Mac側で後から実行)
- 推定所要時間: 12-16時間
