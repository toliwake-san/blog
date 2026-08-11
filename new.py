#!/usr/bin/env python3
"""
新しいページをつくる。ページに種類はない。タグとリンクだけで構造ができる。

    python3 new.py "夕方の坂道"           日記など、日付のあるページ
    python3 new.py "銀河鉄道の夜" 本       タグをつけてつくる（本の登録はこれ）
    python3 new.py "宮沢賢治" 人 --grid    一覧ページ（タグづけされたページが並ぶ）

すでにあるページ名を指定した場合は、そのファイルを開きます。
"""

import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(ROOT, "pages")


def open_in_editor(path):
    if sys.platform == "darwin":
        try:
            subprocess.run(["open", "-t", path], check=False)
        except Exception:
            pass


def main():
    args = [a for a in sys.argv[1:]]
    flags = {a for a in args if a.startswith("--")}
    args = [a for a in args if not a.startswith("--")]

    if not args:
        title = datetime.now().strftime("%Y-%m-%d")
    else:
        title = args[0].strip()
    tags = ", ".join(args[1:])

    # ファイル名に使えない文字だけ落とす
    name = re.sub(r'[/\\:*?"<>|]', "-", title).strip() or "無題"
    os.makedirs(PAGES_DIR, exist_ok=True)
    path = os.path.join(PAGES_DIR, name + ".md")

    if os.path.exists(path):
        print(f"※ すでにあります: pages/{name}.md")
        open_in_editor(path)
        return

    layout = "grid" if "--grid" in flags else ("table" if "--table" in flags else "page")
    date = "" if layout != "page" else datetime.now().strftime("%Y-%m-%d")
    visibility = "unlisted" if "--unlisted" in flags else "public"
    permanent = "false" if "--note" in flags else "true"

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""---
title: {title}
date: {date}
tags: {tags}
layout: {layout}
visibility: {visibility}
permanent: {permanent}
---

""")

    print(f"✓ pages/{name}.md を作成しました")
    print(f'  他のページから [[{title}]] と書けば、ここにリンクとバックリンクが集まります。')
    open_in_editor(path)


if __name__ == "__main__":
    main()
