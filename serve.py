#!/usr/bin/env python3
"""
つきなみ文庫 編集サーバー（外部ライブラリ不要 / Python 3.8+）

    python3 serve.py          http://localhost:8000/edit で書ける
    python3 serve.py 9000     ポートを変える

やること
    - docs/ をそのまま配信する（サイトの確認）
    - /edit でブラウザ上の編集画面を出す
    - 保存されたら pages/ に .md を書いて build.py を走らせる

将来レンタルサーバーへ移すときは、この API 部分を CGI にすれば同じことができます。
安全のため 127.0.0.1（自分のパソコンの中）だけで待ち受けます。
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(ROOT, "pages")
IMAGES_DIR = os.path.join(ROOT, "static", "images")
OUT_DIR = os.path.join(ROOT, "docs")

# 取り込んだ画像の長辺がこれを超えていたら縮小する（0 で縮小しない）
# macOS 標準の sips を使うので、無い環境ではそのまま保存されます
MAX_IMAGE_EDGE = 2000

sys.path.insert(0, ROOT)
import build as B  # noqa: E402

# フォームで扱う項目と、その既定値
FIELDS = ["title", "date", "updated", "tags", "layout", "size", "visibility",
          "permanent", "face", "cover", "spine", "home"]

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".xml": "application/xml; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".avif": "image/avif", ".heic": "image/heic", ".json": "application/json",
}

_build_lock = threading.Lock()


def rebuild():
    with _build_lock:
        try:
            B.build()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def safe_name(name):
    name = re.sub(r'[/\\:*?"<>|]', "-", name).strip()
    name = name.lstrip(".")
    return name or "無題"


def page_path(rel):
    """pages/ の外に出ないことを確かめてから絶対パスを返す。"""
    full = os.path.abspath(os.path.join(PAGES_DIR, rel))
    if not full.startswith(os.path.abspath(PAGES_DIR) + os.sep):
        raise ValueError("パスが不正です")
    return full


def list_pages():
    out = []
    for dirpath, _, filenames in os.walk(PAGES_DIR):
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, PAGES_DIR).replace(os.sep, "/")
            with open(full, encoding="utf-8") as f:
                meta, body = B.parse_front_matter(f.read())
            out.append({
                "path": rel,
                "title": meta.get("title") or rel[:-3],
                "date": meta.get("date", ""),
                "updated": meta.get("updated", ""),
                "tags": meta.get("tags", ""),
                "empty": not body.strip(),
                "mtime": os.path.getmtime(full),
            })
    out.sort(key=lambda p: (p["updated"] or p["date"] or "", p["mtime"]), reverse=True)
    return out


def read_page(rel):
    with open(page_path(rel), encoding="utf-8") as f:
        meta, body = B.parse_front_matter(f.read())
    known = {k: meta.get(k, "") for k in FIELDS}
    extra = {k: v for k, v in meta.items() if k not in FIELDS}
    return {"path": rel, "meta": known, "extra": extra, "body": body}


def body_changed(rel, body):
    """前回保存した本文と変わっていたら True。更新日の自動記録に使う。"""
    try:
        full = page_path(rel)
    except ValueError:
        return False
    if not os.path.exists(full):
        return bool(body.strip())
    with open(full, encoding="utf-8") as f:
        _, old = B.parse_front_matter(f.read())
    return old.strip() != body.strip()


def write_page(rel, meta, extra, body):
    lines = []
    for k in FIELDS:
        v = str(meta.get(k, "")).strip()
        if v or k in ("title", "date", "tags"):
            lines.append(f"{k}: {v}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    text = "---\n" + "\n".join(lines) + "\n---\n\n" + body.strip() + "\n"
    full = page_path(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".svg")


def list_images():
    if not os.path.isdir(IMAGES_DIR):
        return []
    out = []
    for name in os.listdir(IMAGES_DIR):
        full = os.path.join(IMAGES_DIR, name)
        if name.startswith(".") or not os.path.isfile(full):
            continue
        if not name.lower().endswith(IMG_EXT):
            continue
        out.append({"name": name, "size": os.path.getsize(full),
                    "mtime": os.path.getmtime(full)})
    out.sort(key=lambda i: i["mtime"], reverse=True)
    return out


def unique_image_name(name):
    name = os.path.basename(name).replace("/", "-").replace("\\", "-").lstrip(".")
    name = re.sub(r"\s+", " ", name).strip() or "image.png"
    if not name.lower().endswith(IMG_EXT):
        name += ".png"
    stem, ext = os.path.splitext(name)
    n, out = 2, name
    while os.path.exists(os.path.join(IMAGES_DIR, out)):
        out = f"{stem}-{n}{ext}"
        n += 1
    return out


def shrink_image(path):
    """大きすぎる画像を縮める。macOS の sips があるときだけ働く。"""
    if not MAX_IMAGE_EDGE or not shutil.which("sips"):
        return False
    try:
        r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                           capture_output=True, text=True, timeout=30)
        nums = [int(x) for x in re.findall(r":\s*(\d+)", r.stdout)]
        if not nums or max(nums) <= MAX_IMAGE_EDGE:
            return False
        subprocess.run(["sips", "-Z", str(MAX_IMAGE_EDGE), path],
                       capture_output=True, timeout=60)
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def save_image(name, b64):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    data = base64.b64decode(b64.split(",")[-1])
    out = unique_image_name(name)
    full = os.path.join(IMAGES_DIR, out)
    with open(full, "wb") as f:
        f.write(data)
    return {"name": out, "shrunk": shrink_image(full),
            "size": os.path.getsize(full)}


def preview_html(body):
    """本文のプレビュー。[[ ]] は実際のリンク解決をしないので印だけ付ける。"""
    html = B.md_to_html(body)
    titles = {p["title"] for p in list_pages()}

    def mark(m):
        name = m.group(1).strip()
        label = (m.group(2) or name).strip()
        known = name in titles or B.to_slug(B.to_uri(name)) in {
            B.to_slug(B.to_uri(t)) for t in titles
        }
        cls = "wikilink" if known else "wikilink new"
        hint = "" if known else " title=\"保存すると新しく作られます\""
        return f'<span class="{cls}"{hint}>{label}</span>'

    return B.outside_code(html, lambda t: B.LINK_RE.sub(mark, t))


def publish():
    script = os.path.join(ROOT, "publish.sh")
    if not os.path.exists(script):
        return {"ok": False, "log": "publish.sh がありません"}
    try:
        r = subprocess.run(["bash", script], cwd=ROOT, capture_output=True,
                           text=True, timeout=180)
        return {"ok": r.returncode == 0, "log": (r.stdout + r.stderr).strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "log": "時間がかかりすぎたので中止しました"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # ---------------------------------------------------------- 返す
    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def not_found(self):
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h1>404</h1><p><a href='/edit'>編集画面へ</a></p>".encode("utf-8"))

    # ---------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)

        if path in ("/edit", "/edit/"):
            return self.send_bytes(EDITOR_HTML.encode("utf-8"), MIME[".html"])
        if path == "/api/pages":
            return self.send_json({"pages": list_pages()})
        if path == "/api/page":
            try:
                return self.send_json(read_page(q.get("path", [""])[0]))
            except (OSError, ValueError) as e:
                return self.send_json({"error": str(e)}, 400)
        if path == "/api/images":
            return self.send_json({"images": list_images()})
        if path.startswith("/raw/images/"):
            # static/images をビルドを待たずに直接見せる（サムネイル用）
            name = os.path.basename(path[len("/raw/images/"):])
            full = os.path.join(IMAGES_DIR, name)
            if not os.path.isfile(full):
                return self.not_found()
            ext = os.path.splitext(full)[1].lower()
            with open(full, "rb") as f:
                return self.send_bytes(f.read(), MIME.get(ext, "application/octet-stream"))

        # それ以外は docs/ を配信
        rel = path.lstrip("/") or "index.html"
        full = os.path.abspath(os.path.join(OUT_DIR, rel))
        if not full.startswith(os.path.abspath(OUT_DIR)):
            return self.not_found()
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.exists(full):
            return self.not_found()
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            return self.send_bytes(f.read(), MIME.get(ext, "application/octet-stream"))

    # ---------------------------------------------------------- POST
    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "読み取れませんでした"}, 400)

        if path == "/api/save":
            try:
                meta = dict(data.get("meta", {}))
                body = data.get("body", "")
                # 本文が変わっていたら更新日を今日にする（欄を直せば手動でも指定できる）
                if body_changed(data["path"], body):
                    meta["updated"] = datetime.now().strftime("%Y-%m-%d")
                write_page(data["path"], meta, data.get("extra", {}), body)
            except (OSError, ValueError, KeyError) as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"saved": True, "updated": meta.get("updated", ""),
                                   **rebuild()})

        if path == "/api/new":
            name = safe_name(data.get("title", ""))
            rel = name + ".md"
            if os.path.exists(page_path(rel)):
                return self.send_json({"error": "同じ名前のページがあります", "path": rel}, 409)
            meta = {"title": data.get("title", name),
                    "date": data.get("date", ""), "tags": data.get("tags", ""),
                    "layout": "page", "size": "文庫", "visibility": "public",
                    "permanent": "true", "face": "false"}
            write_page(rel, meta, {}, "")
            return self.send_json({"path": rel, **rebuild()})

        if path == "/api/upload":
            try:
                return self.send_json(save_image(data.get("name", ""), data.get("data", "")))
            except (OSError, ValueError, base64.binascii.Error) as e:
                return self.send_json({"error": f"取り込めませんでした: {e}"}, 400)

        if path == "/api/preview":
            return self.send_json({"html": preview_html(data.get("body", ""))})

        if path == "/api/build":
            return self.send_json(rebuild())

        if path == "/api/publish":
            return self.send_json(publish())

        return self.not_found()


EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>編集 — つきなみ文庫</title>
<style>
:root{
  --ink:#1a1a1a; --ink-soft:#6b6b6b; --ink-faint:#a8a8a8;
  --rule:#e8e6e2; --paper:#fff; --tint:#f7f6f3;
  --sans:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
  --serif:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",Georgia,serif;
  --mono:ui-monospace,"SF Mono",Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--ink);background:var(--paper);
  height:100vh;display:grid;grid-template-columns:250px 1fr;overflow:hidden}

/* 左 */
aside{border-right:1px solid var(--rule);display:flex;flex-direction:column;min-height:0;background:var(--tint)}
.brand{font-family:var(--serif);font-size:.95rem;letter-spacing:.1em;padding:1.1rem 1rem .8rem}
.side-act{display:flex;gap:.4rem;padding:0 1rem .7rem}
.side-act button{flex:1}
#q{margin:0 1rem .7rem;width:calc(100% - 2rem)}
#list{flex:1;overflow-y:auto;padding:0 .5rem 1rem}
.item{padding:.5rem .6rem;border-radius:5px;cursor:pointer;line-height:1.4}
.item:hover{background:#fff}
.item.on{background:var(--ink);color:#fff}
.item .t{font-family:var(--serif);font-size:.84rem;display:block;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .d{font-size:.63rem;letter-spacing:.06em;color:var(--ink-faint);font-variant-numeric:tabular-nums}
.item.on .d{color:rgba(255,255,255,.6)}
.item .dot{color:var(--ink-faint);font-size:.63rem}

/* 右 */
main{display:flex;flex-direction:column;min-height:0;min-width:0}
.bar{display:flex;align-items:center;gap:.5rem;padding:.7rem 1.2rem;border-bottom:1px solid var(--rule);flex-wrap:wrap}
.bar .grow{flex:1}
.path{font-family:var(--mono);font-size:.68rem;color:var(--ink-faint)}
#msg{font-size:.72rem;color:var(--ink-soft)}
.pane{flex:1;display:grid;grid-template-columns:1fr;min-height:0}
.pane.split{grid-template-columns:1fr 1fr}
.editor{display:flex;flex-direction:column;min-height:0;min-width:0}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:.5rem .8rem;padding:1rem 1.2rem;border-bottom:1px solid var(--rule)}
.meta label{display:flex;flex-direction:column;gap:.2rem;font-size:.65rem;
  letter-spacing:.08em;color:var(--ink-faint)}
.meta .wide{grid-column:1/-1}
input,select,textarea,button{font-family:inherit;font-size:.82rem;color:var(--ink)}
input,select{border:1px solid var(--rule);border-radius:4px;padding:.35rem .5rem;background:#fff;width:100%}
input:focus,select:focus,textarea:focus{outline:1px solid var(--ink-faint);outline-offset:-1px}
#title{font-family:var(--serif);font-size:1rem}
textarea{flex:1;border:0;padding:1.4rem 1.6rem;resize:none;width:100%;
  font-family:var(--serif);font-size:1rem;line-height:2;letter-spacing:.02em}
button{background:#fff;border:1px solid var(--rule);border-radius:5px;
  padding:.35rem .8rem;cursor:pointer;transition:border-color .15s,background .15s}
button:hover{border-color:var(--ink-faint)}
button.primary{background:var(--ink);border-color:var(--ink);color:#fff}
button.primary:hover{opacity:.85}
button:disabled{opacity:.4;cursor:default}
#preview{border-left:1px solid var(--rule);overflow-y:auto;padding:1.4rem 1.6rem;
  font-family:var(--serif);font-size:.95rem;line-height:2;display:none}
.pane.split #preview{display:block}
#preview img{max-width:100%;height:auto}
#preview .embed{position:relative;height:0;padding-bottom:56.25%;background:var(--tint);border-radius:4px}
#preview .embed iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
#preview .linkcard{display:block;border:1px solid var(--rule);border-radius:6px;padding:.6rem .8rem;
  text-decoration:none;font-family:var(--sans);font-size:.75rem}
#preview .wikilink{border-bottom:1px solid var(--ink-faint)}
#preview .wikilink.new{color:var(--ink-soft);border-bottom:1px dashed var(--ink-faint)}
#preview .wikilink.new::after{content:"＋";font-size:.6em;vertical-align:super;color:var(--ink-faint)}
#preview figcaption{font-family:var(--sans);font-size:.7rem;color:var(--ink-faint);margin-top:.5rem}
#preview .mgn{position:relative;font-size:.75rem;line-height:1.8;color:var(--ink-soft);
  background:var(--tint);border-radius:3px;padding:.7rem .9rem .7rem 1.8rem;margin:.4rem 0 1.2rem}
#preview .mgn-num{position:absolute;left:.7rem;top:.8rem;font-size:.6rem;color:var(--ink-faint)}
#preview .mgn-body>:first-child{margin-top:0}
#preview .mgn-body>:last-child{margin-bottom:0}
#preview .mgn-ref{font-size:.62em;color:var(--ink-faint);cursor:default}
#preview .mgn-toggle{display:none}
.empty-state{padding:3rem 1.6rem;color:var(--ink-faint);font-size:.85rem}

/* 画像 */
#drawer{display:none;border-top:1px solid var(--rule);background:var(--tint);
  padding:.8rem 1.2rem 1rem;max-height:260px;overflow-y:auto}
#drawer.on{display:block}
.drawer-head{display:flex;align-items:center;gap:.6rem;margin-bottom:.7rem}
.drawer-head .grow{flex:1}
.drawer-head .hint{font-size:.68rem;color:var(--ink-faint)}
#imgs{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:.6rem}
.img{background:#fff;border:1px solid var(--rule);border-radius:5px;overflow:hidden;
  cursor:pointer;transition:border-color .15s}
.img:hover{border-color:var(--ink-faint)}
.img .thumb{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;background:var(--tint)}
.img .n{font-size:.6rem;color:var(--ink-soft);padding:.25rem .35rem;line-height:1.35;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.img .acts{display:flex;border-top:1px solid var(--rule)}
.img .acts button{flex:1;border:0;border-radius:0;background:none;font-size:.6rem;
  padding:.22rem 0;color:var(--ink-faint)}
.img .acts button:hover{background:var(--tint);color:var(--ink)}
.img .acts button+button{border-left:1px solid var(--rule)}
textarea.drop{outline:2px dashed var(--ink-faint);outline-offset:-6px;background:var(--tint)}
#log{font-family:var(--mono);font-size:.68rem;color:var(--ink-soft);white-space:pre-wrap;
  padding:.6rem 1.2rem;border-top:1px solid var(--rule);max-height:8rem;overflow-y:auto;display:none}
</style>
</head>
<body>
<aside>
  <div class="brand">つきなみ文庫</div>
  <div class="side-act">
    <button id="new">新規</button>
    <button id="pub">公開</button>
  </div>
  <input id="q" placeholder="さがす">
  <div id="list"></div>
</aside>

<main>
  <div class="bar">
    <span class="path" id="path">—</span>
    <span class="grow"></span>
    <span id="msg"></span>
    <button id="imgbtn">画像</button>
    <button id="split">プレビュー</button>
    <button id="view">見る</button>
    <button id="save" class="primary">保存</button>
  </div>
  <div class="pane" id="pane">
    <div class="editor">
      <div class="meta" id="meta">
        <label class="wide">タイトル<input id="title"></label>
        <label>日付<input id="date" placeholder="2026-08-20"></label>
        <label>更新日<input id="updated" placeholder="保存すると自動"></label>
        <label>タグ（カンマ区切り）<input id="tags"></label>
        <label>判型
          <select id="size">
            <option>文庫</option><option>新書</option><option>単行本</option>
            <option>ハードカバー</option><option>大型本</option>
          </select>
        </label>
        <label>レイアウト
          <select id="layout"><option>page</option><option>grid</option>
            <option>table</option><option>list</option></select>
        </label>
        <label>公開範囲
          <select id="visibility"><option>public</option><option>unlisted</option>
            <option>private</option></select>
        </label>
        <label>面陳<select id="face"><option>false</option><option>true</option></select></label>
        <label>permanent<select id="permanent"><option>true</option><option>false</option></select></label>
        <label>背表紙の短縮名<input id="spine"></label>
        <label>表紙画像<input id="cover" placeholder="images/xxx.jpg"></label>
      </div>
      <textarea id="body" placeholder="ここに書く…（行頭の &gt;&gt; で、直前の段落の脇に注を置けます）" spellcheck="false"></textarea>
      <div id="drawer">
        <div class="drawer-head">
          <button id="pick">パソコンから選ぶ</button>
          <input type="file" id="file" accept="image/*" multiple hidden>
          <span class="grow"></span>
          <span class="hint">クリックで本文に挿入。textareaに画像をドロップ、または貼り付けでも入ります</span>
        </div>
        <div id="imgs"></div>
      </div>
    </div>
    <div id="preview"></div>
  </div>
  <div id="log"></div>
</main>

<script>
var cur = null, pages = [], dirty = false, timer = null;
var $ = function (id) { return document.getElementById(id); };
var META = ['title','date','updated','tags','layout','size','visibility','permanent','face','cover','spine'];

function msg(t, keep) {
  $('msg').textContent = t;
  if (!keep) setTimeout(function(){ if ($('msg').textContent === t) $('msg').textContent = ''; }, 2600);
}
function log(t) { var l = $('log'); l.textContent = t; l.style.display = t ? 'block' : 'none'; }

function api(url, body) {
  var o = body ? { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) } : {};
  return fetch(url, o).then(function (r) { return r.json(); });
}

function loadList(keep) {
  return api('/api/pages').then(function (d) {
    pages = d.pages; render();
    if (!keep && !cur && pages.length) open(pages[0].path);
  });
}

function render() {
  var q = $('q').value.trim().toLowerCase();
  var html = '';
  pages.forEach(function (p) {
    if (q && p.title.toLowerCase().indexOf(q) < 0 && p.path.toLowerCase().indexOf(q) < 0) return;
    html += '<div class="item' + (p.path === cur ? ' on' : '') + '" data-p="' + encodeURIComponent(p.path) + '">'
      + '<span class="t">' + esc(p.title) + '</span>'
      + '<span class="d">' + (p.updated || p.date || '—') + (p.empty ? ' <span class="dot">·空</span>' : '') + '</span></div>';
  });
  $('list').innerHTML = html;
  Array.prototype.forEach.call($('list').children, function (el) {
    el.onclick = function () { open(decodeURIComponent(el.dataset.p)); };
  });
}

function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

function open(path) {
  if (dirty && !confirm('保存していない変更があります。移動しますか？')) return;
  api('/api/page?path=' + encodeURIComponent(path)).then(function (d) {
    if (d.error) { msg(d.error); return; }
    cur = d.path; window.extra = d.extra || {};
    $('path').textContent = 'pages/' + d.path;
    META.forEach(function (k) { if ($(k)) $(k).value = d.meta[k] || ''; });
    if (!$('size').value) $('size').value = '文庫';
    if (!$('layout').value) $('layout').value = 'page';
    if (!$('visibility').value) $('visibility').value = 'public';
    if (!$('permanent').value) $('permanent').value = 'true';
    if (!$('face').value) $('face').value = 'false';
    $('body').value = d.body;
    dirty = false; render(); preview();
  });
}

function save() {
  if (!cur) return;
  var meta = {}; META.forEach(function (k) { if ($(k)) meta[k] = $(k).value.trim(); });
  $('save').disabled = true; msg('保存中…', true);
  api('/api/save', { path: cur, meta: meta, extra: window.extra || {}, body: $('body').value })
    .then(function (d) {
      $('save').disabled = false;
      if (d.error || d.ok === false) { msg('失敗'); log(d.error || 'ビルドに失敗しました'); return; }
      dirty = false;
      if (d.updated && $('updated')) $('updated').value = d.updated;
      msg('保存しました'); log(''); loadList(true);
    });
}

function preview() {
  if (!$('pane').classList.contains('split')) return;
  api('/api/preview', { body: $('body').value }).then(function (d) {
    $('preview').innerHTML = d.html || '';
  });
}

$('save').onclick = save;
$('split').onclick = function () { $('pane').classList.toggle('split'); preview(); };
$('view').onclick = function () {
  if (!cur) return;
  var t = ($('title').value || cur.replace(/\.md$/, '')).replace(/ /g, '_');
  window.open('/' + encodeURIComponent(t) + '.html', '_blank');
};
$('q').oninput = render;
$('body').oninput = function () {
  dirty = true;
  clearTimeout(timer); timer = setTimeout(preview, 400);
};
META.forEach(function (k) { if ($(k)) $(k).oninput = function () { dirty = true; }; });

$('new').onclick = function () {
  var t = prompt('ページ名（本のタイトルや日記の題）');
  if (!t) return;
  var d = new Date(), p = function (n) { return ('0' + n).slice(-2); };
  api('/api/new', { title: t, date: d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) })
    .then(function (r) {
      if (r.error) { msg(r.error); if (r.path) open(r.path); return; }
      cur = null; dirty = false;
      loadList(true).then(function () { open(r.path); $('body').focus(); });
    });
};

/* ---------------------------------------------------------- 画像 */
function insert(text) {
  var t = $('body'), s = t.selectionStart, e = t.selectionEnd, v = t.value;
  var before = v.slice(0, s), after = v.slice(e);
  if (before && !/\n\n$/.test(before)) before += before.endsWith('\n') ? '\n' : '\n\n';
  if (after && !/^\n\n/.test(after)) after = (after.startsWith('\n') ? '\n' : '\n\n') + after;
  t.value = before + text + after;
  var pos = (before + text).length;
  t.focus(); t.setSelectionRange(pos, pos);
  dirty = true; preview();
}

function kb(n) { return n > 1048576 ? (n/1048576).toFixed(1) + 'MB' : Math.round(n/1024) + 'KB'; }

function loadImages() {
  return api('/api/images').then(function (d) {
    var h = '';
    (d.images || []).forEach(function (im) {
      var n = esc(im.name), u = '/raw/images/' + encodeURIComponent(im.name);
      h += '<div class="img" data-n="' + encodeURIComponent(im.name) + '">'
        + '<img class="thumb" src="' + u + '" alt="" loading="lazy">'
        + '<div class="n" title="' + n + ' · ' + kb(im.size) + '">' + n + '</div>'
        + '<div class="acts"><button data-a="wide">大きく</button>'
        + '<button data-a="cover">表紙に</button></div></div>';
    });
    $('imgs').innerHTML = h || '<span class="hint">まだ画像がありません</span>';
    Array.prototype.forEach.call($('imgs').children, function (el) {
      if (!el.dataset.n) return;
      var name = decodeURIComponent(el.dataset.n);
      el.onclick = function (ev) {
        var a = ev.target.dataset ? ev.target.dataset.a : null;
        if (a === 'cover') { $('cover').value = 'images/' + name; dirty = true; msg('表紙に設定'); return; }
        var alt = '';
        insert('![' + alt + '](images/' + name + (a === 'wide' ? ' wide' : '') + ')');
      };
    });
  });
}

function upload(file) {
  return new Promise(function (res, rej) {
    var r = new FileReader();
    r.onload = function () {
      api('/api/upload', { name: file.name || 'pasted.png', data: r.result })
        .then(function (d) {
          if (d.error) { msg(d.error); rej(); return; }
          msg(d.shrunk ? '取り込みました（縮小 ' + kb(d.size) + '）' : '取り込みました');
          res(d.name);
        });
    };
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

function uploadAll(files) {
  var list = Array.prototype.filter.call(files, function (f) { return f.type.indexOf('image/') === 0; });
  if (!list.length) return;
  msg('取り込み中…', true);
  list.reduce(function (chain, f) {
    return chain.then(function () {
      return upload(f).then(function (name) { insert('![](images/' + name + ')'); });
    });
  }, Promise.resolve()).then(loadImages);
}

$('imgbtn').onclick = function () {
  var on = $('drawer').classList.toggle('on');
  $('imgbtn').classList.toggle('primary', on);
  if (on) loadImages();
};
$('pick').onclick = function () { $('file').click(); };
$('file').onchange = function () { uploadAll(this.files); this.value = ''; };

var ta = $('body');
ta.addEventListener('dragover', function (e) { e.preventDefault(); ta.classList.add('drop'); });
ta.addEventListener('dragleave', function () { ta.classList.remove('drop'); });
ta.addEventListener('drop', function (e) {
  e.preventDefault(); ta.classList.remove('drop');
  if (e.dataTransfer.files.length) uploadAll(e.dataTransfer.files);
});
ta.addEventListener('paste', function (e) {
  var items = (e.clipboardData || {}).items || [], files = [];
  for (var i = 0; i < items.length; i++) {
    if (items[i].kind === 'file') { var f = items[i].getAsFile(); if (f) files.push(f); }
  }
  if (files.length) { e.preventDefault(); uploadAll(files); }
});

$('pub').onclick = function () {
  if (!confirm('GitHubに公開しますか？')) return;
  msg('公開中…', true); $('pub').disabled = true;
  api('/api/publish', {}).then(function (r) {
    $('pub').disabled = false;
    msg(r.ok ? '公開しました' : '失敗しました');
    log(r.log || '');
  });
};

document.addEventListener('keydown', function (e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); save(); }
});
window.addEventListener('beforeunload', function (e) {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadList();
</script>
</body>
</html>
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    rebuild()
    url = f"http://localhost:{port}/edit"
    print(f"→ {url}   (Ctrl+C で終了)")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました")


if __name__ == "__main__":
    main()
