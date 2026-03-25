#!/bin/bash
# 外部からサーバーの進捗を確認するスクリプト
# Usage: bash scripts/check_progress.sh
#
# Mac から実行:
#   ssh pws-ubuntu-server@100.104.225.55 'cat ~/projects/kanto-re-potential/data/batch_checkpoint.json' | python3 -m json.tool
#   ssh pws-ubuntu-server@100.104.225.55 'tail -20 ~/projects/kanto-re-potential/data/batch_progress.txt'

SERVER="pws-ubuntu-server@100.104.225.55"
PROJECT="~/projects/kanto-re-potential"

echo "=== バッチ計算 進捗確認 ==="
echo ""

# 1. 進捗サマリー
echo "--- チェックポイント サマリー ---"
ssh $SERVER "cat $PROJECT/data/batch_checkpoint.json 2>/dev/null" | python3 -c "
import json, sys
try:
    cp = json.load(sys.stdin)
    done = sum(1 for v in cp.values() if v.get('status') == 'completed')
    failed = sum(1 for v in cp.values() if 'failed_step' in v and v.get('status') != 'completed')
    in_progress = len(cp) - done - failed
    print(f'  完了: {done} | 失敗: {failed} | 進行中: {in_progress} | 合計: {len(cp)}')
    print()
    for k, v in cp.items():
        status = v.get('status', 'in_progress')
        if 'failed_step' in v and status != 'completed':
            status = f'FAILED at {v[\"failed_step\"]}'
        steps = len(v.get('completed_steps', []))
        print(f'  {k:25s}: {status:20s} ({steps}/5 steps)')
except:
    print('  チェックポイントファイルが見つかりません')
" 2>/dev/null

echo ""

# 2. 最新の進捗ログ
echo "--- 最新の進捗 (直近10行) ---"
ssh $SERVER "tail -10 $PROJECT/data/batch_progress.txt 2>/dev/null" || echo "  進捗ファイルがまだありません"

echo ""

# 3. ディスク使用量
echo "--- ディスク使用量 ---"
ssh $SERVER "du -sh $PROJECT/data/ $PROJECT/logs/ $PROJECT/output/ 2>/dev/null" || echo "  確認できません"

echo ""

# 4. tmuxセッション確認
echo "--- tmuxセッション ---"
ssh $SERVER "tmux list-sessions 2>/dev/null" || echo "  tmuxセッションなし"
