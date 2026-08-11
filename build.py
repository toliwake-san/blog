#!/usr/bin/env python3
"""
つきなみ文庫 ビルドスクリプト（外部ライブラリ不要 / Python 3.8+）

posts/*.md と books/*.md を読み込んで docs/ に静的サイトを書き出します。

    python3 build.py           # ビルド
    python3 build.py --serve   # ビルドしてローカル確認 (http://localhost:8000)
"""

import hashlib
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime

# ---------------------------------------------------------------- 設定
SITE_TITLE = "つきなみ文庫"
SITE_DESCRIPTION = "読んだ本と、日々のこと。"
AUTHOR = "asage"
BASE_URL = ""  # 公開URL（例 https://ユーザー名.github.io/blog）。RSS用

ROOT = os.path.dirname(os.path.abspath(__file__))
POSTS_DIR = os.path.join(ROOT, "posts")
BOOKS_DIR = os.path.join(ROOT, "books")
PAGES_DIR = os.path.join(ROOT, "pages")
STATIC_DIR = os.path.join(ROOT, "static")
# GitHub Pages が公開できるのは "/" か "/docs" だけなので docs にしてある
OUT_DIR = os.path.join(ROOT, "docs")


# ---------------------------------------------------------------- Markdown
def _inline(text):
    stash = []

    def _stash(m):
        stash.append(m.group(1))
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1" loading="lazy">', text)
    text = re.sub(r"(?<!\!)\[([^\[\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)

    def _unstash(m):
        return "<code>" + html.escape(stash[int(m.group(1))], quote=False) + "</code>"

    return re.sub(r"\x00(\d+)\x00", _unstash, text)


def md_to_html(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf), quote=False) + "</code></pre>")
            continue
        if re.fullmatch(r"(\*\s*){3,}|(-\s*){3,}|(_\s*){3,}", s):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            out.append(f"<h{min(len(m.group(1)) + 1, 6)}>{_inline(m.group(2))}</h{min(len(m.group(1)) + 1, 6)}>")
            i += 1
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + md_to_html("\n".join(buf)) + "</blockquote>")
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*+]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{t}</li>" for t in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{t}</li>" for t in items) + "</ol>")
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#{1,4}\s|>|```|[-*+]\s|\d+\.\s)", lines[i]
        ):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline("<br>".join(buf)).replace("&lt;br&gt;", "<br>") + "</p>")
    return "\n".join(out)


def parse_front_matter(raw):
    meta, body = {}, raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip('"').strip("'")
            body = parts[2]
    return meta, body.strip()


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def csv_list(s):
    return [x.strip() for x in (s or "").replace("、", ",").split(",") if x.strip()]


def short_hash(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- 読み込み
def load_books():
    books = {}
    if not os.path.isdir(BOOKS_DIR):
        return books
    for name in sorted(os.listdir(BOOKS_DIR)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        with open(os.path.join(BOOKS_DIR, name), encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        title = meta.get("title") or name[:-3]
        base = name[:-3]
        slug = meta.get("slug") or (base if re.fullmatch(r"[A-Za-z0-9_-]+", base) else "b-" + short_hash(title))
        books[title] = {
            "kind": "book",
            "title": title,
            "author": meta.get("author", ""),
            "year": meta.get("year", ""),
            "cover": meta.get("cover", ""),
            "link": meta.get("link", ""),
            "rating": meta.get("rating", ""),
            "aliases": csv_list(meta.get("aliases", "")),
            "note": md_to_html(body) if body else "",
            "slug": slug,
            "url": f"books/{slug}.html",
            "mentions": [],
        }
    return books


def load_posts():
    posts = []
    if not os.path.isdir(POSTS_DIR):
        return posts
    for name in sorted(os.listdir(POSTS_DIR)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = os.path.join(POSTS_DIR, name)
        with open(path, encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        if meta.get("draft", "").lower() in ("true", "yes", "1"):
            continue
        slug = meta.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name[:-3])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", slug):
            slug = "p-" + short_hash(name)
        date = meta.get("date") or name[:10]
        try:
            dt = datetime.strptime(date[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.fromtimestamp(os.path.getmtime(path))
        posts.append(
            {
                "kind": "post",
                "slug": slug,
                "title": meta.get("title") or slug,
                "date": dt,
                "date_str": dt.strftime("%Y年%m月%d日"),
                "short_date": dt.strftime("%Y.%m.%d"),
                "iso": dt.strftime("%Y-%m-%d"),
                "tags": csv_list(meta.get("tags", "")),
                "book_tags": csv_list(meta.get("books", "")),
                "cover": meta.get("cover", ""),
                "raw_html": md_to_html(body),
                "url": f"posts/{slug}.html",
                "excerpt": meta.get("excerpt", ""),
                "links_out": [],
                "backlinks": [],
            }
        )
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


# ---------------------------------------------------------------- リンク解決
LINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
CODE_RE = re.compile(r"<pre>.*?</pre>|<code>.*?</code>", re.S)


def outside_code(text, fn):
    """<code> や <pre> の中身には手を触れずに置換する。"""
    out, last = [], 0
    for m in CODE_RE.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def build_graph(posts, books):
    """[[...]] を解決し、本への言及とバックリンクを集める。"""
    index = {}  # 表示名 -> ノード
    for b in books.values():
        index[b["title"]] = b
        for a in b["aliases"]:
            index.setdefault(a, b)
    for p in posts:
        index.setdefault(p["title"], p)

    for p in posts:
        p["mention_targets"] = {}

        # 段落ごとに、どのノードに言及しているかを記録（本ページの引用に使う）
        for para in re.findall(r"<p>.*?</p>", p["raw_html"], re.S):
            para = CODE_RE.sub("", para)
            names = [m.group(1).strip() for m in LINK_RE.finditer(para)]
            for n in names:
                node = index.get(n)
                if node and node is not p:
                    quote = strip_tags(LINK_RE.sub(lambda m: m.group(2) or m.group(1), para))
                    p["mention_targets"].setdefault(node["title"], []).append(quote)

        # 本文リンクを <a> に置き換え
        def repl(m, _p=p):
            name = m.group(1).strip()
            label = (m.group(2) or name).strip()
            node = index.get(name)
            if not node or node is _p:
                return f'<span class="link-missing" title="リンク先がありません">{html.escape(label)}</span>'
            if node["title"] not in [x["title"] for x in _p["links_out"]]:
                _p["links_out"].append(node)
            cls = "wikilink book" if node["kind"] == "book" else "wikilink"
            return f'<a class="{cls}" href="../{node["url"]}">{html.escape(label)}</a>'

        p["html"] = outside_code(p["raw_html"], lambda t: LINK_RE.sub(repl, t))

        plain = strip_tags(p["html"])
        if not p["excerpt"]:
            p["excerpt"] = plain[:110] + ("…" if len(plain) > 110 else "")
        p["length"] = len(plain)

        # front matter の books: も参照に加える
        for name in p["book_tags"]:
            node = index.get(name)
            if node and node["kind"] == "book" and node["title"] not in [x["title"] for x in p["links_out"]]:
                p["links_out"].append(node)

    # バックリンクと、本ごとの言及を集約
    by_title = {p["title"]: p for p in posts}
    for p in posts:
        for node in p["links_out"]:
            if node["kind"] == "book":
                quotes = p["mention_targets"].get(node["title"]) or [p["excerpt"]]
                node["mentions"].append({"post": p, "quotes": quotes})
            else:
                target = by_title.get(node["title"])
                if target is not None and p["title"] not in [x["title"] for x in target["backlinks"]]:
                    target["backlinks"].append(p)

    for b in books.values():
        b["mentions"].sort(key=lambda m: m["post"]["date"], reverse=True)
    return index


# ---------------------------------------------------------------- テンプレート
def layout(title, body, depth=0, is_home=False, desc="", extra_head="", extra_body=""):
    up = "../" * depth
    site = html.escape(SITE_TITLE)
    head_title = site if is_home else f"{html.escape(title)} — {site}"
    description = html.escape(desc or SITE_DESCRIPTION)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{head_title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{head_title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="{'website' if is_home else 'article'}">
<link rel="alternate" type="application/rss+xml" title="{site}" href="{up}feed.xml">
<link rel="stylesheet" href="{up}style.css">{extra_head}
</head>
<body>
<header class="site-header">
  <a class="site-title" href="{up}index.html">{site}</a>
  <nav class="site-nav">
    <a href="{up}index.html">記事</a>
    <a href="{up}books.html">本</a>
    <a href="{up}about.html">について</a>
    <a href="{up}feed.xml">RSS</a>
    <a class="icon-link" href="{up}network.html" aria-label="つながり">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3">
        <circle cx="6" cy="17" r="2.4"/><circle cx="18" cy="17" r="2.4"/><circle cx="12" cy="6" r="2.4"/>
        <path d="M7.6 15.2 10.6 8M13.5 8l3 7M8.4 17h7.2"/>
      </svg>
    </a>
  </nav>
</header>
<main>
{body}
</main>
<footer class="site-footer">
  <p>© {datetime.now().year} {html.escape(AUTHOR)} — {site}</p>
</footer>{extra_body}
</body>
</html>
"""


def book_thumb(b, up="", cls="thumb"):
    """表紙画像があればそれを、なければ書影代わりの文字組みを出す。"""
    if b["cover"]:
        src = b["cover"] if b["cover"].startswith("http") else up + b["cover"].lstrip("/")
        return f'<div class="{cls} has-img"><img src="{html.escape(src)}" alt="{html.escape(b["title"])}" loading="lazy"></div>'
    return (
        f'<div class="{cls} no-img"><span class="spine-title">{html.escape(b["title"])}</span>'
        f'<span class="spine-author">{html.escape(b["author"])}</span></div>'
    )


# ---------------------------------------------------------------- 各ページ
def render_index(posts, books):
    tag_counts = {}
    for p in posts:
        for t in p["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    pills = [f'<button class="pill is-on" data-tag="*">すべて<sup>{len(posts)}</sup></button>']
    for t, c in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
        pills.append(f'<button class="pill" data-tag="{html.escape(t)}">{html.escape(t)}<sup>{c}</sup></button>')

    cards = []
    for p in posts:
        size = "s" if p["length"] < 160 else ("l" if p["length"] > 900 else "m")
        cover = ""
        if p["cover"]:
            src = p["cover"] if p["cover"].startswith("http") else p["cover"].lstrip("/")
            cover = f'<div class="card-cover"><img src="{html.escape(src)}" alt="" loading="lazy"></div>'
        refs = "".join(
            f'<span class="chip">{html.escape(n["title"])}</span>'
            for n in p["links_out"] if n["kind"] == "book"
        )
        excerpt = p["excerpt"] if size != "s" else p["excerpt"][:60]
        cards.append(
            f"""<article class="card size-{size}{' has-cover' if cover else ''}" data-tags="{html.escape(' '.join(p['tags']))}">
  <a href="{p['url']}">
    {cover}
    <div class="card-body">
      <time datetime="{p['iso']}">{p['short_date']}</time>
      <h3>{html.escape(p['title'])}</h3>
      <p>{html.escape(excerpt)}</p>
    </div>
  </a>
  <div class="card-foot">{refs}{''.join(f'<span class="tag">{html.escape(t)}</span>' for t in p['tags'])}</div>
</article>"""
        )

    js = """<script>
document.querySelectorAll('.pill').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var tag = btn.dataset.tag;
    document.querySelectorAll('.pill').forEach(function (b) { b.classList.toggle('is-on', b === btn); });
    document.querySelectorAll('.card').forEach(function (c) {
      var on = tag === '*' || (' ' + c.dataset.tags + ' ').indexOf(' ' + tag + ' ') >= 0;
      c.style.display = on ? '' : 'none';
    });
  });
});
</script>"""

    body = f"""<div class="pills">{''.join(pills)}</div>
<div class="grid">
{''.join(cards)}
</div>"""
    return layout(SITE_TITLE, body, is_home=True, extra_body=js)


def render_post(p, newer, older):
    tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in p["tags"])

    out_html = ""
    if p["links_out"]:
        items = "".join(
            f'<li><a href="../{n["url"]}">{html.escape(n["title"])}</a></li>' for n in p["links_out"]
        )
        out_html = f'<section class="rel"><h2 aria-label="この記事から">→</h2><ul>{items}</ul></section>'

    back_html = ""
    if p["backlinks"]:
        items = "".join(
            f'<li><a href="{b["slug"]}.html">{html.escape(b["title"])}</a></li>' for b in p["backlinks"]
        )
        back_html = f'<section class="rel"><h2 aria-label="この記事に触れている記事">←</h2><ul>{items}</ul></section>'

    nav = []
    if newer:
        nav.append(f'<a class="prev" href="{newer["slug"]}.html">← {html.escape(newer["title"])}</a>')
    if older:
        nav.append(f'<a class="next" href="{older["slug"]}.html">{html.escape(older["title"])} →</a>')
    nav_html = f'<nav class="post-nav">{"".join(nav)}</nav>' if nav else ""

    body = f"""<article class="post">
  <header class="post-header">
    <time datetime="{p['iso']}">{p['date_str']}</time>
    <h1>{html.escape(p['title'])}</h1>
    <div class="tags">{tags}</div>
  </header>
  <div class="post-body">
{p['html']}
  </div>
</article>
{out_html}{back_html}
{nav_html}
<p class="back"><a href="../index.html">一覧へ戻る</a></p>"""
    return layout(p["title"], body, depth=1, desc=p["excerpt"])


def render_books_index(books):
    items = []
    for b in sorted(books.values(), key=lambda x: (-len(x["mentions"]), x["title"])):
        n = len(b["mentions"])
        items.append(
            f"""<a class="shelf-item" href="{b['url']}">
  {book_thumb(b)}
  <div class="shelf-meta">
    <span class="shelf-title">{html.escape(b['title'])}</span>
    <span class="shelf-author">{html.escape(b['author'])}</span>
    <span class="shelf-count">{n}</span>
  </div>
</a>"""
        )
    empty = "" if items else '<p class="empty">—</p>'
    body = f'<div class="shelf">{"".join(items)}</div>{empty}'
    return layout("本", body, desc="読んだ本の一覧")


def render_book(b):
    meta_bits = []
    if b["author"]:
        meta_bits.append(html.escape(b["author"]))
    if b["year"]:
        meta_bits.append(html.escape(b["year"]))
    link = f'<p class="book-link"><a href="{html.escape(b["link"])}">この本について →</a></p>' if b["link"] else ""

    mentions = []
    for m in b["mentions"]:
        p = m["post"]
        quotes = "".join(f"<p>{html.escape(q)}</p>" for q in m["quotes"])
        mentions.append(
            f"""<article class="mention">
  <blockquote>{quotes}</blockquote>
  <a class="mention-src" href="../{p['url']}">{p['short_date']}　{html.escape(p['title'])} →</a>
</article>"""
        )
    if not mentions:
        mentions.append('<p class="empty">—</p>')

    body = f"""<div class="book-head">
  {book_thumb(b, up="../", cls="thumb big")}
  <div class="book-info">
    <h1>{html.escape(b['title'])}</h1>
    <p class="book-meta">{'　'.join(meta_bits)}</p>
    {f'<div class="book-note">{b["note"]}</div>' if b["note"] else ''}
    {link}
  </div>
</div>
<section class="mentions">
  {''.join(mentions)}
</section>
<p class="back"><a href="../books.html">本の一覧へ戻る</a></p>"""
    return layout(b["title"], body, depth=1, desc=f"{b['title']}（{b['author']}）について書いた記事のまとめ")


def render_network(posts, books):
    nodes, edges = [], []
    idx = {}
    for p in posts:
        idx[("post", p["title"])] = len(nodes)
        nodes.append({"id": p["title"], "t": "post", "u": p["url"], "w": max(1, len(p["backlinks"]) + 1)})
    for b in books.values():
        idx[("book", b["title"])] = len(nodes)
        nodes.append({"id": b["title"], "t": "book", "u": b["url"], "w": max(1, len(b["mentions"]) + 1)})
    for p in posts:
        a = idx[("post", p["title"])]
        for n in p["links_out"]:
            key = (n["kind"], n["title"])
            if key in idx:
                edges.append([a, idx[key]])

    data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    body = '<div class="graph-wrap"><canvas id="graph"></canvas></div>'
    script = f"""<script>
var DATA = {data};
(function () {{
  var cv = document.getElementById('graph'), ctx = cv.getContext('2d');
  var N = DATA.nodes.map(function (n, i) {{
    return {{ d: n, x: Math.cos(i) * 120 + Math.random() * 40, y: Math.sin(i * 1.7) * 120 + Math.random() * 40, vx: 0, vy: 0 }};
  }});
  var E = DATA.edges;
  var W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2), hover = null;
  function size() {{
    W = cv.parentNode.clientWidth; H = Math.max(380, Math.min(560, W * 0.72));
    cv.width = W * DPR; cv.height = H * DPR; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }}
  function step() {{
    for (var i = 0; i < N.length; i++) {{
      for (var j = i + 1; j < N.length; j++) {{
        var dx = N[j].x - N[i].x, dy = N[j].y - N[i].y, d2 = dx * dx + dy * dy + 0.01;
        var f = 900 / d2, d = Math.sqrt(d2);
        N[i].vx -= f * dx / d; N[i].vy -= f * dy / d;
        N[j].vx += f * dx / d; N[j].vy += f * dy / d;
      }}
    }}
    E.forEach(function (e) {{
      var a = N[e[0]], b = N[e[1]], dx = b.x - a.x, dy = b.y - a.y;
      var d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d - 80) * 0.008;
      a.vx += f * dx / d; a.vy += f * dy / d; b.vx -= f * dx / d; b.vy -= f * dy / d;
    }});
    N.forEach(function (n) {{
      n.vx -= n.x * 0.004; n.vy -= n.y * 0.004;
      n.vx *= 0.86; n.vy *= 0.86; n.x += n.vx; n.y += n.vy;
    }});
  }}
  function radius(n) {{ return 4 + Math.min(9, n.d.w * 1.6); }}
  function draw() {{
    var css = getComputedStyle(document.documentElement);
    var rule = css.getPropertyValue('--rule').trim() || '#e8e6e2';
    var ink = css.getPropertyValue('--ink').trim() || '#1a1a1a';
    var soft = css.getPropertyValue('--ink-faint').trim() || '#a8a8a8';
    ctx.clearRect(0, 0, W, H);
    ctx.save(); ctx.translate(W / 2, H / 2);
    ctx.strokeStyle = rule; ctx.lineWidth = 1;
    E.forEach(function (e) {{
      ctx.beginPath(); ctx.moveTo(N[e[0]].x, N[e[0]].y); ctx.lineTo(N[e[1]].x, N[e[1]].y); ctx.stroke();
    }});
    N.forEach(function (n) {{
      ctx.beginPath(); ctx.arc(n.x, n.y, radius(n), 0, 6.284);
      ctx.fillStyle = '#fff'; ctx.fill();
      ctx.strokeStyle = soft; ctx.lineWidth = 1; ctx.stroke();
    }});
    ctx.font = '11px -apple-system, sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = soft;
    N.forEach(function (n) {{ if (n.d.w > 1 || n === hover) ctx.fillText(n.d.id, n.x, n.y - radius(n) - 5); }});
    if (hover) {{
      ctx.fillStyle = ink; ctx.font = '12px -apple-system, sans-serif';
      ctx.fillText(hover.d.id, hover.x, hover.y - radius(hover) - 5);
    }}
    ctx.restore();
  }}
  function pick(ev) {{
    var r = cv.getBoundingClientRect(), mx = ev.clientX - r.left - W / 2, my = ev.clientY - r.top - H / 2;
    var best = null;
    N.forEach(function (n) {{
      var d = Math.hypot(n.x - mx, n.y - my);
      if (d < radius(n) + 7 && (!best || d < Math.hypot(best.x - mx, best.y - my))) best = n;
    }});
    return best;
  }}
  cv.addEventListener('mousemove', function (e) {{ hover = pick(e); cv.style.cursor = hover ? 'pointer' : 'default'; }});
  cv.addEventListener('click', function (e) {{ var n = pick(e); if (n) location.href = n.d.u; }});
  window.addEventListener('resize', size);
  size();
  var t = 0;
  (function loop() {{ if (t++ < 400) step(); draw(); requestAnimationFrame(loop); }})();
}})();
</script>"""
    return layout("つながり", body, desc="記事と本の参照関係", extra_body=script)


def render_page(md_name, fallback_title):
    path = os.path.join(PAGES_DIR, md_name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        title = meta.get("title", fallback_title)
        content = md_to_html(body)
    else:
        title, content = fallback_title, "<p>準備中です。</p>"
    return layout(
        title,
        f'<article class="post"><header class="post-header"><h1>{html.escape(title)}</h1></header>'
        f'<div class="post-body">{content}</div></article>',
    )


def render_feed(posts):
    items = []
    for p in posts[:20]:
        link = f"{BASE_URL}/{p['url']}" if BASE_URL else p["url"]
        items.append(
            f"""  <item>
    <title>{html.escape(p['title'])}</title>
    <link>{html.escape(link)}</link>
    <guid isPermaLink="false">{html.escape(p['slug'])}</guid>
    <pubDate>{p['date'].strftime('%a, %d %b %Y 00:00:00 +0900')}</pubDate>
    <description>{html.escape(p['excerpt'])}</description>
  </item>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{html.escape(SITE_TITLE)}</title>
  <link>{html.escape(BASE_URL or 'index.html')}</link>
  <description>{html.escape(SITE_DESCRIPTION)}</description>
  <language>ja</language>
{chr(10).join(items)}
</channel>
</rss>
"""


# ---------------------------------------------------------------- ビルド
def write(rel, text):
    path = os.path.join(OUT_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build():
    books = load_books()
    posts = load_posts()
    build_graph(posts, books)

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    write("index.html", render_index(posts, books))
    for i, p in enumerate(posts):
        write(f"posts/{p['slug']}.html", render_post(p, posts[i - 1] if i > 0 else None,
                                                     posts[i + 1] if i + 1 < len(posts) else None))
    write("books.html", render_books_index(books))
    for b in books.values():
        write(f"books/{b['slug']}.html", render_book(b))
    write("network.html", render_network(posts, books))
    write("about.html", render_page("about.md", "について"))
    write("feed.xml", render_feed(posts))

    if os.path.isdir(STATIC_DIR):
        for name in os.listdir(STATIC_DIR):
            src, dst = os.path.join(STATIC_DIR, name), os.path.join(OUT_DIR, name)
            shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()

    missing = sum(p["html"].count('class="link-missing"') for p in posts)
    print(f"✓ 記事 {len(posts)} / 本 {len(books)} をビルドしました → docs/")
    if missing:
        print(f"  ※ 行き先のない [[リンク]] が {missing} 件あります")
    return posts, books


if __name__ == "__main__":
    build()
    if "--serve" in sys.argv:
        import http.server
        import socketserver

        os.chdir(OUT_DIR)
        print("→ http://localhost:8000  (Ctrl+C で終了)")
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", 8000), http.server.SimpleHTTPRequestHandler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
