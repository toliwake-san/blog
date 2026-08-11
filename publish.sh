#!/bin/bash
# ビルドして公開する。使い方: ./publish.sh  または  ./publish.sh "コミットメッセージ"
set -e
cd "$(dirname "$0")"

python3 build.py

if [ ! -d .git ]; then
  echo "※ まだ git の設定が済んでいません。START.md の Step 3 を見てください。"
  exit 1
fi

MSG="${1:-$(date '+%Y-%m-%d') 更新}"

git add -A
if git diff --cached --quiet; then
  echo "新しい変更はありません。"
else
  git commit -m "$MSG"
fi

# 未送信のコミットがあれば送る
if [ -n "$(git log @{u}..HEAD 2>/dev/null)" ] || ! git rev-parse @{u} >/dev/null 2>&1; then
  git push
  echo "✓ 公開しました  https://toliwake-san.github.io/blog/"
else
  echo "✓ すでに最新です"
fi
