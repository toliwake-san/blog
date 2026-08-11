#!/bin/bash
# ビルドして GitHub に公開する。使い方: ./publish.sh  または  ./publish.sh "コミットメッセージ"
set -e
cd "$(dirname "$0")"

python3 build.py

if [ ! -d .git ]; then
  echo "※ まだ git の設定が済んでいません。README の「4. 公開する」を見てください。"
  exit 1
fi

MSG="${1:-$(date '+%Y-%m-%d') 更新}"

git add -A
if git diff --cached --quiet; then
  echo "変更はありません。"
  exit 0
fi

git commit -m "$MSG"
git push
echo "✓ 公開しました"
