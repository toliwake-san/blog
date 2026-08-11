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

# GitHub上で直接編集した内容があれば先に取り込む
if git rev-parse @{u} >/dev/null 2>&1; then
  git fetch -q origin
  if [ -n "$(git log HEAD..@{u} 2>/dev/null)" ]; then
    echo "GitHub側の変更を取り込みます…"
    git stash -q -u 2>/dev/null || true
    if ! git rebase -q @{u}; then
      echo "※ 自動で統合できませんでした。競合しているファイルを直してから ./publish.sh をやり直してください。"
      exit 1
    fi
    git stash pop -q 2>/dev/null || true
    git add -A
  fi
fi
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
