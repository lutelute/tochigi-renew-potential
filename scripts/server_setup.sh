#!/bin/bash
# サーバー初期セットアップスクリプト
# Usage: ssh pws-ubuntu-server@100.104.225.55 'bash -s' < scripts/server_setup.sh

set -e

echo "=== サーバーセットアップ開始 ==="

# プロジェクトディレクトリ
mkdir -p ~/projects
cd ~/projects

# 1. kanto-re-potential clone (feature/all-japan ブランチ)
if [ ! -d "kanto-re-potential" ]; then
    echo "[1] Cloning kanto-re-potential..."
    git clone -b feature/all-japan https://github.com/lutelute/kanto-re-potential.git
else
    echo "[1] kanto-re-potential already exists, pulling..."
    cd kanto-re-potential
    git fetch origin
    git checkout feature/all-japan
    git pull origin feature/all-japan
    cd ~/projects
fi

# 2. All-Japan-Grid clone
if [ ! -d ~/All-Japan-Grid-ref ]; then
    echo "[2] Cloning All-Japan-Grid-ref..."
    git clone https://github.com/lutelute/All-Japan-Grid-ref.git ~/All-Japan-Grid-ref
else
    echo "[2] All-Japan-Grid-ref already exists, pulling..."
    cd ~/All-Japan-Grid-ref && git pull && cd ~/projects
fi

# 3. Python依存パッケージ
echo "[3] Installing Python packages..."
pip3 install --user geopandas shapely rasterio numpy pandas scipy 2>/dev/null || \
pip3 install geopandas shapely rasterio numpy pandas scipy

# 4. CLAUDE.server.md をサーバー側CLAUDE.mdとして配置
echo "[4] Setting up CLAUDE.md for server..."
cd ~/projects/kanto-re-potential
cp CLAUDE.server.md CLAUDE.md

# 5. 環境変数設定
echo "[5] Setting environment variables..."
export ALL_JAPAN_GRID_DIR=~/All-Japan-Grid-ref/data

# 6. ディレクトリ作成
mkdir -p data logs

# 7. テスト
echo "[6] Testing configuration..."
python3 -c "
import sys
sys.path.insert(0, 'src')
from config import PREFECTURES
print(f'OK: {len(PREFECTURES)} prefectures loaded')
print(f'First 5: {list(PREFECTURES.keys())[:5]}')
"

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "次のステップ:"
echo "  tmux new -s alljapan"
echo "  cd ~/projects/kanto-re-potential"
echo "  export ALL_JAPAN_GRID_DIR=~/All-Japan-Grid-ref/data"
echo ""
echo "  # テスト実行 (30m, 福井県のみ):"
echo "  python3 src/batch_all_japan.py -p fukui --resolution 30"
echo ""
echo "  # 全国実行 (5m):"
echo "  python3 src/batch_all_japan.py --resolution 5 --resume"
echo ""
echo "  # Claude自律実行:"
echo "  claude --dangerously-skip-permissions"
