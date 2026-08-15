#!/usr/bin/env python3
"""
つきなみ文庫 ビルドスクリプト（外部ライブラリ不要 / Python 3.8+）

    python3 build.py           ビルド
    python3 build.py --serve   ビルドしてローカル確認 (http://localhost:8000)

設計:
  - 投稿タイプもカテゴリの木構造もない。pages/ 以下はすべて同じスキーマのページ
  - 構造はリンクによってのみ生まれる
      tag link  (A is B)     ... frontmatter の tags
      body link (A refers B) ... 本文中の [[ ]]
  - ページの性格は layout（page / grid / table / list）で決まる
"""

import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from urllib.parse import quote, unquote

# ---------------------------------------------------------------- 設定
SITE_TITLE = "つきなみ文庫"
SITE_DESCRIPTION = "読んだ本と、日々のこと。"
AUTHOR = "asage"
BASE_URL = "https://toliwake-san.github.io/blog"

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(ROOT, "pages")
STATIC_DIR = os.path.join(ROOT, "static")
OUT_DIR = os.path.join(ROOT, "docs")

LAYOUTS = ("page", "grid", "table", "list")

# ---- 本棚ビューの設定 ----------------------------------------------
# 判型と背の高さ(px)。frontmatter の size: で選ぶ。既定は文庫
BOOK_SIZES = {
    "文庫": 250,
    "新書": 292,
    "単行本": 330,
    "ハードカバー": 372,
    "大型本": 430,
}
DEFAULT_SIZE = "文庫"
SHELF_ROW = max(BOOK_SIZES.values()) + 26   # 棚1段の高さ
SPINE_MIN, SPINE_MAX = 20, 72               # 背の厚み(px)の下限と上限


def css_version():
    """CSSの中身からバージョン文字列を作る。ブラウザの古いキャッシュ対策。"""
    path = os.path.join(STATIC_DIR, "style.css")
    if not os.path.exists(path):
        return "0"
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


CSS_V = ""
STATIC_INDEX = {}   # 小文字パス -> 実際のパス
MISSING_ASSETS = []


def akey(s):
    """照合用のキー。macOS はファイル名を NFD で持つので NFC に揃えて小文字化する。"""
    return unicodedata.normalize("NFC", s).lower()


def load_static_index():
    """static/ の中身を控えておく。参照とファイル名の食い違いを直すのに使う。"""
    STATIC_INDEX.clear()
    if not os.path.isdir(STATIC_DIR):
        return
    for dirpath, _, filenames in os.walk(STATIC_DIR):
        for name in filenames:
            if name in (".DS_Store", "Thumbs.db"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), STATIC_DIR).replace(os.sep, "/")
            STATIC_INDEX[akey(rel)] = rel


def resolve_asset(path):
    """
    画像などの参照を、実在するファイル名に合わせる。手元では見えるのに
    公開すると404になる事故を防ぐためのもの。吸収するのは2種類の食い違い。

      1. 拡張子の大文字小文字（.JPG と .jpg）
         macOS は区別しないが GitHub Pages(Linux) は区別する
      2. 濁点などのUnicode表現（NFD と NFC）
         Finder 由来のファイル名と、エディタで打った文字はここがずれる
    """
    if not path or path.startswith(("http://", "https://", "//", "data:")):
        return path
    clean = unquote(path).lstrip("/")
    actual = STATIC_INDEX.get(akey(clean))
    if actual:
        return actual
    MISSING_ASSETS.append(clean)
    return clean


def spine_width(chars):
    """厚みは文字数の対数で決める。長文でも際限なく太らないように。"""
    w = SPINE_MIN + 32 * math.log10(1 + chars / 110)
    return int(round(min(max(w, SPINE_MIN), SPINE_MAX)))


# ---------------------------------------------------------------- Markdown
def _inline(text):
    stash = []

    def _stash(m):
        stash.append(m.group(1))
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = html.escape(text, quote=False)
    # ![キャプション](images/x.jpg) / 幅を広げたいときは末尾に "wide"
    # ファイル名に半角スペースが入っていても動くようにしてある
    def _img(m):
        src, opt = m.group(2).strip(), (m.group(3) or "").strip('"').strip()
        # 引用符を忘れて (images/x.jpg wide) と書かれても拾う
        if opt and opt != "wide":      # 想定外の指定はパスの一部として扱う
            src, opt = f"{src} {opt}", ""
        if not src.startswith(("http://", "https://", "data:", "//")):
            src = quote(resolve_asset(src), safe="/._~()-")
        cls = ' class="wide"' if opt else ""
        return f'<img src="{src}" alt="{m.group(1)}"{cls} loading="lazy">'

    text = re.sub(r'!\[([^\]]*)\]\(\s*([^)"]+?)(?:\s+("?\w+"?))?\s*\)', _img, text)
    text = re.sub(r"(?<!\!)\[([^\[\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)

    def _unstash(m):
        return "<code>" + html.escape(stash[int(m.group(1))], quote=False) + "</code>"

    return re.sub(r"\x00(\d+)\x00", _unstash, text)


URL_ONLY_RE = re.compile(r"^<?(https?://[^\s<>\"]+)>?$")


def _iframe(src, title, ratio="56.25%", allow="encrypted-media; picture-in-picture; clipboard-write"):
    return (f'<div class="embed" style="padding-bottom:{ratio}">'
            f'<iframe src="{html.escape(src)}" title="{html.escape(title)}" loading="lazy" '
            f'frameborder="0" allow="{allow}" allowfullscreen referrerpolicy="strict-origin-when-cross-origin">'
            f'</iframe></div>')


def embed_html(url):
    """行に単独で置かれたURLを埋め込みに変換する。対応外なら None。"""
    m = (re.match(r"https?://(?:www\.)?youtube\.com/watch\?(?:[^#]*&)?v=([\w-]+)", url)
         or re.match(r"https?://youtu\.be/([\w-]+)", url)
         or re.match(r"https?://(?:www\.)?youtube\.com/(?:shorts|embed|live)/([\w-]+)", url))
    if m:
        t = re.search(r"[?&]t=(\d+)", url)
        q = f"?start={t.group(1)}" if t else ""
        # トラッキングを減らすため nocookie ドメインを使う
        return _iframe(f"https://www.youtube-nocookie.com/embed/{m.group(1)}{q}", "YouTube")

    m = re.match(r"https?://(?:www\.)?vimeo\.com/(\d+)", url)
    if m:
        return _iframe(f"https://player.vimeo.com/video/{m.group(1)}", "Vimeo")

    m = re.match(r"https?://open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|episode|show)/(\w+)", url)
    if m:
        h = "152" if m.group(1) == "track" else "352"
        return (f'<div class="embed fixed" style="height:{h}px">'
                f'<iframe src="https://open.spotify.com/embed/{m.group(1)}/{m.group(2)}" '
                f'title="Spotify" loading="lazy" frameborder="0" '
                f'allow="encrypted-media; clipboard-write"></iframe></div>')

    m = re.match(r"https?://(?:www\.)?(?:soundcloud\.com|snd\.sc)/\S+", url)
    if m:
        return (f'<div class="embed fixed" style="height:166px">'
                f'<iframe src="https://w.soundcloud.com/player/?url={quote(url, safe="")}&color=%231a1a1a&'
                f'hide_related=true&show_comments=false&show_user=true&show_reposts=false" '
                f'title="SoundCloud" loading="lazy" frameborder="0"></iframe></div>')

    m = re.match(r"https?://(?:www\.)?google\.com/maps/embed\?\S+", url)
    if m:
        return _iframe(url, "Google Maps", ratio="66%")
    return None


def link_card(url):
    host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0]
    rest = url.split(host, 1)[-1].rstrip("/")
    rest = (rest[:60] + "…") if len(rest) > 60 else rest
    return (f'<a class="linkcard" href="{html.escape(url)}" rel="noopener">'
            f'<span class="lc-host">{html.escape(host)}</span>'
            f'<span class="lc-path">{html.escape(rest) or "/"}</span></a>')


def figure_or_p(inner):
    """段落が画像1枚だけなら figure にして alt をキャプションにする。"""
    m = re.fullmatch(r'<img src="([^"]*)" alt="([^"]*)"([^>]*)>', inner.strip())
    if not m:
        return f"<p>{inner}</p>"
    cap = f"<figcaption>{m.group(2)}</figcaption>" if m.group(2) else ""
    cls = ' class="wide"' if 'class="wide"' in m.group(3) else ""
    return f'<figure{cls}><img src="{m.group(1)}" alt="{m.group(2)}"{m.group(3)}>{cap}</figure>'


def md_to_html(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        line, s = lines[i], lines[i].strip()
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
            lv = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lv}>{_inline(m.group(2))}</h{lv}>")
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
        # 行に単独で置かれたURL → 埋め込み、またはリンクカード
        m = URL_ONLY_RE.match(s)
        if m:
            out.append(embed_html(m.group(1)) or link_card(m.group(1)))
            i += 1
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#{1,4}\s|>|```|[-*+]\s|\d+\.\s)", lines[i]
        ) and not URL_ONLY_RE.match(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if not buf:
            continue
        out.append(figure_or_p(_inline("<br>".join(buf)).replace("&lt;br&gt;", "<br>")))
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


def truthy(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("true", "yes", "1", "on")


# ---------------------------------------------------------------- 識別子
def to_uri(path):
    """path → uri。半角スペースを _ に。"""
    return path.replace(" ", "_")


def to_slug(uri):
    """uri → slug。DB検索用の正規化キー。"""
    return uri.lower()


def url_of(page, from_depth=0):
    up = "../" * from_depth
    return up + quote(page["uri"], safe="/") + ".html"


def asset(path, from_depth=0):
    if path.startswith(("http://", "https://", "//")):
        return path
    return "../" * from_depth + quote(resolve_asset(path), safe="/._~()-")


# ---------------------------------------------------------------- 読み込み
def new_page(path, meta=None, body_md="", virtual=False, mtime=None):
    meta = meta or {}
    uri = to_uri(path)
    layout = (meta.get("layout") or "page").lower()
    if layout not in LAYOUTS:
        layout = "page"

    date_raw = meta.get("date", "")
    dt = None
    if date_raw:
        try:
            dt = datetime.strptime(date_raw[:10], "%Y-%m-%d")
        except ValueError:
            dt = None
    if dt is None:
        dt = datetime.fromtimestamp(mtime) if mtime else datetime(1970, 1, 1)

    visibility = (meta.get("visibility") or "public").lower()
    if visibility not in ("public", "unlisted", "private"):
        visibility = "public"

    # permanent は明示がなければ true。layout が page 以外なら常に true
    permanent = truthy(meta.get("permanent"), default=True) or layout != "page"

    return {
        "path": path,
        "uri": uri,
        "slug": to_slug(uri),
        "depth": path.count("/"),
        "title": meta.get("title") or path.rsplit("/", 1)[-1],
        "date": dt,
        "has_date": bool(date_raw),
        "date_str": dt.strftime("%Y年%m月%d日"),
        "short_date": dt.strftime("%Y.%m.%d"),
        "iso": dt.strftime("%Y-%m-%d"),
        "tag_names": csv_list(meta.get("tags", "")),
        "layout": layout,
        "visibility": visibility,
        "permanent": permanent,
        "cover": meta.get("cover", ""),
        "size": meta.get("size", "").strip() or DEFAULT_SIZE,
        "spine": meta.get("spine", "").strip(),   # 背表紙用の短いタイトル
        "face": truthy(meta.get("face"), default=False),  # 面陳（表紙を見せる）
        "on_home": truthy(meta.get("home"), default=True),
        "excerpt_fm": meta.get("excerpt", ""),
        "source": body_md,
        "raw_html": md_to_html(body_md) if body_md else "",
        "virtual": virtual,
        "tag_links": [],       # このページが tag link で指すページ
        "body_links": [],      # このページが body link で指すページ
        "back_tag": [],        # tag link で指されている（= 分類されている）
        "back_body": [],       # body link で言及されている
        "quotes": {},          # 相手slug -> このページ内の言及段落
    }


def load_pages():
    pages = {}
    if not os.path.isdir(PAGES_DIR):
        return pages
    for dirpath, _, filenames in os.walk(PAGES_DIR):
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, PAGES_DIR)[:-3].replace(os.sep, "/")
            with open(full, encoding="utf-8") as f:
                meta, body = parse_front_matter(f.read())
            p = new_page(rel, meta, body, mtime=os.path.getmtime(full))
            if p["visibility"] == "private":
                continue
            pages[p["slug"]] = p
    return pages


# ---------------------------------------------------------------- リンク
LINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
CODE_RE = re.compile(r"<pre>.*?</pre>|<code>.*?</code>", re.S)


def outside_code(text, fn):
    out, last = [], 0
    for m in CODE_RE.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def resolve(pages, by_title, name):
    return pages.get(to_slug(to_uri(name))) or by_title.get(name)


def build_graph(pages):
    """tag link を辿ってページを自動生成し、リンクとバックリンクを張る。"""
    # tags で参照されているのに実体がないページを作る
    added = True
    while added:
        added = False
        for p in list(pages.values()):
            for t in p["tag_names"]:
                s = to_slug(to_uri(t))
                if s not in pages:
                    v = new_page(t, {"layout": "grid", "home": "false"}, virtual=True)
                    pages[v["slug"]] = v
                    added = True

    by_title = {}
    for p in pages.values():
        by_title.setdefault(p["title"], p)

    # tag link
    for p in pages.values():
        for t in p["tag_names"]:
            target = resolve(pages, by_title, t)
            if target and target is not p:
                p["tag_links"].append(target)
                if p["visibility"] == "public":
                    target["back_tag"].append(p)

    # body link
    for p in pages.values():
        for para in re.findall(r"<p>.*?</p>", p["raw_html"], re.S):
            clean = CODE_RE.sub("", para)
            for m in LINK_RE.finditer(clean):
                target = resolve(pages, by_title, m.group(1).strip())
                if target and target is not p:
                    quote_txt = strip_tags(LINK_RE.sub(lambda x: x.group(2) or x.group(1), clean))
                    p["quotes"].setdefault(target["slug"], []).append(quote_txt)

        def repl(m, _p=p):
            name = m.group(1).strip()
            label = (m.group(2) or name).strip()
            target = resolve(pages, by_title, name)
            if not target or target is _p:
                return f'<span class="link-missing">{html.escape(label)}</span>'
            if target not in _p["body_links"]:
                _p["body_links"].append(target)
                if _p["visibility"] == "public":
                    target["back_body"].append(_p)
            return f'<a class="wikilink" href="{url_of(target, _p["depth"])}">{html.escape(label)}</a>'

        body = outside_code(p["raw_html"], lambda t: LINK_RE.sub(repl, t))
        # 面陳の表紙に使う。深さ調整の前（サイト直下からの相対）で取っておく
        m0 = re.search(r'<img [^>]*src="([^"]+)"', body)
        p["first_image"] = m0.group(1) if m0 else ""
        # 画像やリンクの相対パスを深さに合わせる
        if p["depth"]:
            body = re.sub(
                r'(<img [^>]*src=")(?!https?://|\.\./|/|data:)([^"]+)"',
                lambda m: m.group(1) + "../" * p["depth"] + m.group(2) + '"',
                body,
            )
        p["html"] = body

        plain = strip_tags(body)
        p["excerpt"] = p["excerpt_fm"] or (plain[:110] + ("…" if len(plain) > 110 else ""))
        p["length"] = len(plain)

    for p in pages.values():
        for key in ("back_tag", "back_body"):
            p[key].sort(key=lambda x: (x["date"], x["title"]), reverse=True)
    return pages


# ---------------------------------------------------------------- テンプレート
def layout_html(page, pages, body, extra_body="", extra_head=""):
    d = page["depth"] if page else 0
    up = "../" * d
    site = html.escape(SITE_TITLE)
    is_home = page is None
    title = SITE_TITLE if is_home else page["title"]
    head_title = site if is_home else f"{html.escape(title)} — {site}"
    desc = html.escape(SITE_DESCRIPTION if is_home else (page["excerpt"] or SITE_DESCRIPTION))
    robots = "" if is_home or page["permanent"] else '\n<meta name="robots" content="noindex, nofollow">'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{head_title}</title>
<meta name="description" content="{desc}">{robots}
<meta property="og:title" content="{head_title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="{'website' if is_home else 'article'}">
<link rel="alternate" type="application/rss+xml" title="{site}" href="{up}feed.xml">
<link rel="stylesheet" href="{up}style.css?v={CSS_V}">{extra_head}
</head>
<body>
<header class="site-header">
  <a class="site-title" href="{up}index.html">{site}</a>
  <nav class="site-nav">
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


def card_html(p, depth):
    cover = ""
    if p["cover"]:
        cover = f'<div class="card-cover"><img src="{asset(p["cover"], depth)}" alt="" loading="lazy"></div>'
    size = "s" if p["length"] < 160 else ("l" if p["length"] > 900 else "m")
    tags = "".join(f'<span class="tag">{html.escape(t["title"])}</span>' for t in p["tag_links"])
    date = f'<time datetime="{p["iso"]}">{p["short_date"]}</time>' if p["has_date"] else ""
    excerpt = html.escape(p["excerpt"] if size != "s" else p["excerpt"][:60])
    return f"""<article class="card size-{size}{' has-cover' if cover else ''}" data-tags="{html.escape(' '.join(t['title'] for t in p['tag_links']))}">
  <a href="{url_of(p, depth)}">
    {cover}
    <div class="card-body">
      {date}
      <h3>{html.escape(p['title'])}</h3>
      <p>{excerpt}</p>
    </div>
  </a>
  <div class="card-foot">{tags}</div>
</article>"""


def book_html(p, depth):
    """本棚に並ぶ1冊。背表紙、または面陳（表紙を見せる）。"""
    h = BOOK_SIZES.get(p["size"], BOOK_SIZES[DEFAULT_SIZE])
    tags = html.escape(" ".join(t["title"] for t in p["tag_links"]))
    href = url_of(p, depth)
    title = html.escape(p["spine"] or p["title"])

    if p["face"]:
        w = int(round(h * 0.68))
        # cover: は素のパス、first_image は既にエスケープ済みなので扱いを分ける
        if p["cover"]:
            src = p["cover"] if p["cover"].startswith("http") else asset(p["cover"], depth)
        elif p["first_image"]:
            src = p["first_image"] if p["first_image"].startswith("http") \
                else "../" * depth + p["first_image"]
        else:
            src = ""
        inner = (f'<span class="face-img"><img src="{src}" alt="" loading="lazy"></span>'
                 if src else '<span class="face-img blank"></span>')
        date = f'<span class="face-date">{p["short_date"]}</span>' if p["has_date"] else ""
        return (f'<a class="book face" data-tags="{tags}" href="{href}" style="--w:{w}px;--h:{h}px">'
                f'<span class="vol">{inner}<span class="face-cap">'
                f'<span class="face-title">{title}</span>{date}</span></span></a>')

    w = spine_width(p["length"])
    return (f'<a class="book" data-tags="{tags}" href="{href}" style="--w:{w}px;--h:{h}px">'
            f'<span class="vol"><span class="spine-title">{title}</span></span></a>')


def spine_overflow(p):
    """背表紙にタイトルが入りきらなそうなら True。ビルド時の注意用。"""
    if p["face"]:
        return False
    h = BOOK_SIZES.get(p["size"], BOOK_SIZES[DEFAULT_SIZE])
    cols = max(1, (spine_width(p["length"]) - 12) // 16)
    return len(p["spine"] or p["title"]) > cols * int((h - 30) / 15)


def listed(items):
    return [p for p in items if p["visibility"] == "public"]


# ---------------------------------------------------------------- 各ページ
def render_home(pages):
    items = sorted(
        [p for p in pages.values() if p["visibility"] == "public" and p["on_home"]],
        key=lambda p: (p["date"], p["title"]),
        reverse=True,
    )
    counts = {}
    for p in items:
        for t in p["tag_links"]:
            counts[t["title"]] = counts.get(t["title"], 0) + 1
    pills = [f'<button class="pill is-on" data-tag="*">すべて<sup>{len(items)}</sup></button>']
    for t, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        pills.append(f'<button class="pill" data-tag="{html.escape(t)}">{html.escape(t)}<sup>{c}</sup></button>')

    toggle = """<div class="viewtoggle">
  <button data-view="grid" aria-label="ブロックで見る" title="ブロック">
    <svg viewBox="0 0 22 22" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.3">
      <rect x="2.5" y="2.5" width="7" height="8.5" rx="1"/><rect x="12.5" y="2.5" width="7" height="5.5" rx="1"/>
      <rect x="2.5" y="14" width="7" height="5.5" rx="1"/><rect x="12.5" y="11" width="7" height="8.5" rx="1"/>
    </svg>
  </button>
  <button data-view="shelf" aria-label="本棚で見る" title="本棚">
    <svg viewBox="0 0 22 22" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.3">
      <path d="M2 18.6h18" stroke-width="1.5"/>
      <rect x="3" y="6" width="3" height="12.6" rx=".6"/><rect x="7.2" y="4" width="4" height="14.6" rx=".6"/>
      <rect x="12.4" y="7.5" width="2.6" height="11.1" rx=".6"/>
      <path d="M16.4 18.6 17.9 8.2l2.6.5-1.4 9.9z" stroke-linejoin="round"/>
    </svg>
  </button>
</div>"""

    js = """<script>
(function () {
  var KEY = 'tsukinami-view';
  function apply(v) {
    document.documentElement.dataset.view = v;
    try { localStorage.setItem(KEY, v); } catch (e) {}
    document.querySelectorAll('.viewtoggle button').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.view === v);
    });
  }
  document.querySelectorAll('.viewtoggle button').forEach(function (b) {
    b.addEventListener('click', function () { apply(b.dataset.view); });
  });
  apply(document.documentElement.dataset.view || 'shelf');

  document.querySelectorAll('.pill').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tag = btn.dataset.tag;
      document.querySelectorAll('.pill').forEach(function (b) { b.classList.toggle('is-on', b === btn); });
      document.querySelectorAll('.card, .book').forEach(function (c) {
        var on = tag === '*' || (' ' + c.dataset.tags + ' ').indexOf(' ' + tag + ' ') >= 0;
        c.style.display = on ? '' : 'none';
      });
    });
  });
})();
</script>"""
    # 表示切替のちらつきを防ぐため、描画前に前回の選択を読む
    head = (f"\n<style>:root{{--shelf-row-set:{SHELF_ROW}px}}</style>"
            "\n<script>(function(){try{document.documentElement.dataset.view="
            "localStorage.getItem('tsukinami-view')||'shelf';}catch(e){"
            "document.documentElement.dataset.view='shelf';}})();</script>")

    body = (f'<div class="pills">{"".join(pills)}</div>{toggle}'
            f'<div class="shelf view-shelf">{"".join(book_html(p, 0) for p in items)}</div>'
            f'<div class="grid view-grid">{"".join(card_html(p, 0) for p in items)}</div>')
    return layout_html(None, pages, body, extra_body=js, extra_head=head)


def render_page(p, pages):
    d = p["depth"]
    head_tags = "".join(
        f'<a class="tag" href="{url_of(t, d)}">{html.escape(t["title"])}</a>' for t in p["tag_links"]
    )
    date = f'<time datetime="{p["iso"]}">{p["date_str"]}</time>' if p["has_date"] else ""
    note = "" if p["permanent"] else '<span class="note-flag" title="書きかけのメモ">note</span>'

    header = f"""<header class="post-header">
    {date}{note}
    <h1>{html.escape(p['title'])}</h1>
    <div class="tags">{head_tags}</div>
  </header>"""
    body_html = f'<div class="post-body">{p["html"]}</div>' if p["html"] else ""
    out = [f'<article class="post">{header}{body_html}</article>']

    back_tag, back_body = listed(p["back_tag"]), listed(p["back_body"])

    if p["layout"] == "grid":
        out.append(f'<div class="grid">{"".join(card_html(x, d) for x in back_tag)}</div>')
    elif p["layout"] == "table":
        rows = "".join(
            f'<tr><td class="c-date">{x["short_date"] if x["has_date"] else ""}</td>'
            f'<td><a href="{url_of(x, d)}">{html.escape(x["title"])}</a></td>'
            f'<td class="c-tags">{"".join(html.escape(t["title"]) + " " for t in x["tag_links"])}</td></tr>'
            for x in back_tag
        )
        out.append(f'<table class="rows">{rows}</table>')
    elif p["layout"] == "list":
        items = "".join(f'<li><a href="{url_of(x, d)}">{html.escape(x["title"])}</a></li>' for x in back_tag)
        out.append(f'<section class="rel"><h2 aria-label="ここに属するページ">↳</h2><ul>{items}</ul></section>')
    elif back_tag:
        items = "".join(f'<li><a href="{url_of(x, d)}">{html.escape(x["title"])}</a></li>' for x in back_tag)
        out.append(f'<section class="rel"><h2 aria-label="ここに属するページ">↳</h2><ul>{items}</ul></section>')

    # body link のバックリンク = 言及。grid / table では出さない
    if back_body and p["layout"] not in ("grid", "table"):
        blocks = []
        for x in back_body:
            quotes = x["quotes"].get(p["slug"]) or [x["excerpt"]]
            qs = "".join(f"<p>{html.escape(q)}</p>" for q in quotes)
            label = f'{x["short_date"]}　' if x["has_date"] else ""
            blocks.append(
                f'<article class="mention"><blockquote>{qs}</blockquote>'
                f'<a class="mention-src" href="{url_of(x, d)}">{label}{html.escape(x["title"])} →</a></article>'
            )
        out.append(f'<section class="mentions">{"".join(blocks)}</section>')

    if p["body_links"]:
        items = "".join(
            f'<li><a href="{url_of(t, d)}">{html.escape(t["title"])}</a></li>' for t in p["body_links"]
        )
        out.append(f'<section class="rel"><h2 aria-label="このページから">→</h2><ul>{items}</ul></section>')

    out.append(f'<p class="back"><a href="{"../" * d}index.html">一覧へ戻る</a></p>')
    return layout_html(p, pages, "\n".join(out))


def render_network(pages):
    items = [p for p in pages.values() if p["visibility"] == "public"]
    idx = {p["slug"]: i for i, p in enumerate(items)}
    nodes = [{"id": p["title"], "u": url_of(p, 0),
              "w": max(1, len(p["back_tag"]) + len(p["back_body"]) + 1)} for p in items]
    edges = []
    for p in items:
        for t in p["tag_links"] + p["body_links"]:
            if t["slug"] in idx and p["slug"] in idx:
                edges.append([idx[p["slug"]], idx[t["slug"]]])

    data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    script = f"""<script>
var DATA = {data};
(function () {{
  var cv = document.getElementById('graph'), ctx = cv.getContext('2d');
  var N = DATA.nodes.map(function (n, i) {{
    return {{ d: n, x: Math.cos(i) * 120 + Math.random() * 40, y: Math.sin(i * 1.7) * 120 + Math.random() * 40, vx: 0, vy: 0 }};
  }});
  var E = DATA.edges, W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2), hover = null;
  function size() {{
    W = cv.parentNode.clientWidth; H = Math.max(380, Math.min(560, W * 0.72));
    cv.width = W * DPR; cv.height = H * DPR; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }}
  function step() {{
    for (var i = 0; i < N.length; i++) for (var j = i + 1; j < N.length; j++) {{
      var dx = N[j].x - N[i].x, dy = N[j].y - N[i].y, d2 = dx * dx + dy * dy + 0.01;
      var f = 900 / d2, d = Math.sqrt(d2);
      N[i].vx -= f * dx / d; N[i].vy -= f * dy / d; N[j].vx += f * dx / d; N[j].vy += f * dy / d;
    }}
    E.forEach(function (e) {{
      var a = N[e[0]], b = N[e[1]], dx = b.x - a.x, dy = b.y - a.y;
      var d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d - 80) * 0.008;
      a.vx += f * dx / d; a.vy += f * dy / d; b.vx -= f * dx / d; b.vy -= f * dy / d;
    }});
    N.forEach(function (n) {{
      n.vx -= n.x * 0.004; n.vy -= n.y * 0.004; n.vx *= 0.86; n.vy *= 0.86; n.x += n.vx; n.y += n.vy;
    }});
  }}
  function radius(n) {{ return 4 + Math.min(9, n.d.w * 1.6); }}
  function draw() {{
    var css = getComputedStyle(document.documentElement);
    var rule = css.getPropertyValue('--rule').trim() || '#e8e6e2';
    var ink = css.getPropertyValue('--ink').trim() || '#1a1a1a';
    var soft = css.getPropertyValue('--ink-faint').trim() || '#a8a8a8';
    ctx.clearRect(0, 0, W, H); ctx.save(); ctx.translate(W / 2, H / 2);
    ctx.strokeStyle = rule; ctx.lineWidth = 1;
    E.forEach(function (e) {{
      ctx.beginPath(); ctx.moveTo(N[e[0]].x, N[e[0]].y); ctx.lineTo(N[e[1]].x, N[e[1]].y); ctx.stroke();
    }});
    N.forEach(function (n) {{
      ctx.beginPath(); ctx.arc(n.x, n.y, radius(n), 0, 6.284);
      ctx.fillStyle = '#fff'; ctx.fill(); ctx.strokeStyle = soft; ctx.lineWidth = 1; ctx.stroke();
    }});
    ctx.font = '11px -apple-system, sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = soft;
    N.forEach(function (n) {{ if (n.d.w > 1) ctx.fillText(n.d.id, n.x, n.y - radius(n) - 5); }});
    if (hover) {{
      ctx.fillStyle = ink; ctx.font = '12px -apple-system, sans-serif';
      ctx.fillText(hover.d.id, hover.x, hover.y - radius(hover) - 5);
    }}
    ctx.restore();
  }}
  function pick(ev) {{
    var r = cv.getBoundingClientRect(), mx = ev.clientX - r.left - W / 2, my = ev.clientY - r.top - H / 2, best = null;
    N.forEach(function (n) {{
      var d = Math.hypot(n.x - mx, n.y - my);
      if (d < radius(n) + 7 && (!best || d < Math.hypot(best.x - mx, best.y - my))) best = n;
    }});
    return best;
  }}
  cv.addEventListener('mousemove', function (e) {{ hover = pick(e); cv.style.cursor = hover ? 'pointer' : 'default'; }});
  cv.addEventListener('click', function (e) {{ var n = pick(e); if (n) location.href = n.d.u; }});
  window.addEventListener('resize', size); size();
  var t = 0;
  (function loop() {{ if (t++ < 400) step(); draw(); requestAnimationFrame(loop); }})();
}})();
</script>"""
    return layout_html(None, pages, '<div class="graph-wrap"><canvas id="graph"></canvas></div>',
                       extra_body=script)


def render_feed(pages):
    items = sorted(
        [p for p in pages.values() if p["visibility"] == "public" and p["has_date"]],
        key=lambda p: p["date"], reverse=True,
    )[:20]
    entries = []
    for p in items:
        link = f"{BASE_URL}/{quote(p['uri'], safe='/')}.html" if BASE_URL else url_of(p)
        entries.append(f"""  <item>
    <title>{html.escape(p['title'])}</title>
    <link>{html.escape(link)}</link>
    <guid isPermaLink="false">{html.escape(p['slug'])}</guid>
    <pubDate>{p['date'].strftime('%a, %d %b %Y 00:00:00 +0900')}</pubDate>
    <description>{html.escape(p['excerpt'])}</description>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{html.escape(SITE_TITLE)}</title>
  <link>{html.escape(BASE_URL or 'index.html')}</link>
  <description>{html.escape(SITE_DESCRIPTION)}</description>
  <language>ja</language>
{chr(10).join(entries)}
</channel>
</rss>
"""


# ---------------------------------------------------------------- ビルド
def write(rel, text):
    path = os.path.join(OUT_DIR, rel)
    os.makedirs(os.path.dirname(path) or OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build():
    global CSS_V
    CSS_V = css_version()
    load_static_index()
    MISSING_ASSETS.clear()
    pages = build_graph(load_pages())

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    write("index.html", render_home(pages))
    for p in pages.values():
        write(p["uri"] + ".html", render_page(p, pages))
    write("network.html", render_network(pages))
    write("feed.xml", render_feed(pages))

    ignore = shutil.ignore_patterns(".DS_Store", "Thumbs.db")
    if os.path.isdir(STATIC_DIR):
        for name in os.listdir(STATIC_DIR):
            if name in (".DS_Store", "Thumbs.db"):
                continue
            src, dst = os.path.join(STATIC_DIR, name), os.path.join(OUT_DIR, name)
            shutil.copytree(src, dst, ignore=ignore) if os.path.isdir(src) else shutil.copy2(src, dst)
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()

    real = [p for p in pages.values() if not p["virtual"]]
    auto = len(pages) - len(real)
    unlisted = sum(1 for p in pages.values() if p["visibility"] == "unlisted")
    notes = sum(1 for p in pages.values() if not p["permanent"])
    missing = sum(p["html"].count('class="link-missing"') for p in pages.values())
    over = [p["title"] for p in pages.values()
            if p["visibility"] == "public" and p["on_home"] and spine_overflow(p)]

    print(f"✓ {len(pages)} ページ（うち自動生成 {auto}）をビルドしました → docs/")
    if unlisted or notes:
        print(f"  unlisted {unlisted} / note {notes}")
    if missing:
        print(f"  ※ 行き先のない [[リンク]] が {missing} 件あります")
    if over:
        print("  ※ 背表紙に入りきらないかもしれないタイトル（spine: で短い名前を指定できます）:")
        for t in over[:6]:
            print(f"     - {t}")
    if MISSING_ASSETS:
        print("  ※ static/ に見つからない画像があります。ファイル名を確認してください:")
        for a in sorted(set(MISSING_ASSETS))[:8]:
            print(f"     - {a}")
    return pages


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
