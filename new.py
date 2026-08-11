#!/usr/bin/env python3
"""
記事、または本のひな型をつくる。

    python3 new.py                    今日の日付で無題の記事
    python3 new.py "夏の終わりに"      タイトルを指定して記事
    python3 new.py -b "銀河鉄道の夜"   本を登録する
"""

import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
POSTS_DIR = os.path.join(ROOT, "posts")
BOOKS_DIR = os.path.join(ROOT, "books")


def open_in_editor(path):
    if sys.platform == "darwin":
        try:
            subprocess.run(["open", "-t", path], check=False)
        except Exception:
            pass


def ascii_slug(title):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return s if len(s) > 2 else ""


def new_post(title):
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    os.makedirs(POSTS_DIR, exist_ok=True)

    a = ascii_slug(title)
    base = f"{date}-{a}"[:60] if a else date
    path = os.path.join(POSTS_DIR, base + ".md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(POSTS_DIR, f"{base}-{n}.md")
        n += 1

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""---
title: {title}
date: {date}
tags:
books:
cover:
draft: false
---

""")
    print(f"✓ {os.path.relpath(path, ROOT)} を作成しました")
    open_in_editor(path)


def new_book(title):
    os.makedirs(BOOKS_DIR, exist_ok=True)
    a = ascii_slug(title) or "b-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    path = os.path.join(BOOKS_DIR, a + ".md")
    if os.path.exists(path):
        print(f"※ すでにあります: {os.path.relpath(path, ROOT)}")
        open_in_editor(path)
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""---
title: {title}
author:
year:
slug: {a}
aliases:
cover:
link:
---

""")
    print(f"✓ {os.path.relpath(path, ROOT)} を作成しました")
    print(f'  記事のなかで [[{title}]] と書くと、この本のページに集まります。')
    open_in_editor(path)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-b", "--book"):
        title = " ".join(args[1:]).strip()
        if not title:
            print("使い方: python3 new.py -b \"書名\"")
            sys.exit(1)
        new_book(title)
    else:
        new_post(" ".join(args).strip() or "無題")


if __name__ == "__main__":
    main()
