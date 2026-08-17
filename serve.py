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

import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(ROOT, "pages")
OUT_DIR = os.path.join(ROOT, "docs")

sys.path.insert(0, ROOT)
import build as B  # noqa: E402

# フォームで扱う項目と、その既定値
FIELDS = ["title", "date", "tags", "layout", "size", "visibility",
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
                "tags": meta.get("tags", ""),
                "empty": not body.strip(),
                "mtime": os.path.getmtime(full),
            })
    out.sort(key=lambda p: (p["date"] or "", p["mtime"]), reverse=True)
    return out


def read_page(rel):
    with open(page_path(rel), encoding="utf-8") as f:
        meta, body = B.parse_front_matter(f.read())
    known = {k: meta.get(k, "") for k in FIELDS}
    extra = {k: v for k, v in meta.items() if k not in FIELDS}
    return {"path": rel, "meta": known, "extra": extra, "body": body}


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
            d = os.path.join(ROOT, "static", "images")
            names = sorted(n for n in os.listdir(d)) if os.path.isdir(d) else []
            return self.send_json({"images": [n for n in names if not n.startswith(".")]})

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
                write_page(data["path"], data.get("meta", {}),
                           data.get("extra", {}), data.get("body", ""))
            except (OSError, ValueError, KeyError) as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"saved": True, **rebuild()})

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
.empty-state{padding:3rem 1.6rem;color:var(--ink-faint);font-size:.85rem}
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
    <button id="split">プレビュー</button>
    <button id="view">見る</button>
    <button id="save" class="primary">保存</button>
  </div>
  <div class="pane" id="pane">
    <div class="editor">
      <div class="meta" id="meta">
        <label class="wide">タイトル<input id="title"></label>
        <label>日付<input id="date" placeholder="2026-08-20"></label>
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
      <textarea id="body" placeholder="ここに書く…" spellcheck="false"></textarea>
    </div>
    <div id="preview"></div>
  </div>
  <div id="log"></div>
</main>

<script>
var cur = null, pages = [], dirty = false, timer = null;
var $ = function (id) { return document.getElementById(id); };
var META = ['title','date','tags','layout','size','visibility','permanent','face','cover','spine'];

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
      + '<span class="d">' + (p.date || '—') + (p.empty ? ' <span class="dot">·空</span>' : '') + '</span></div>';
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
      dirty = false; msg('保存しました'); log(''); loadList(true);
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
