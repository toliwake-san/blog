# 公開までの手順

上から順に進めれば公開できます。所要 20〜30分。
ターミナルは「アプリケーション → ユーティリティ → ターミナル」で開きます。

サンプルは削除済みで、`posts/2026-08-11-hajimeni.md` が1本だけ入っています。
初回コミットも済んでいるので、Step 0 と Step 3 の `git init` 〜 `commit` は不要です。

---

## Step 0. 手元で動くか確認する

ターミナルに貼り付けて Enter。

```bash
cd ~/Desktop/blog && python3 build.py --serve
```

`→ http://localhost:8000` と出たら、ブラウザでそのアドレスを開きます。
サイトが表示されればOK。確認できたら、ターミナルで `Control + C` を押して止めます。

<details>
<summary>「command not found: python3」と出たとき</summary>

Python が入っていません。ターミナルで `xcode-select --install` を実行するか、
[python.org](https://www.python.org/downloads/) から最新版をインストールしてください。
</details>

---

## Step 1. GitHub のアカウントを作る

1. [github.com](https://github.com) を開く
2. 右上の **Sign up** から登録（メールアドレス・パスワード・ユーザー名）
   - **ユーザー名がURLの一部になります。**`asage` なら `asage.github.io/blog`
3. 送られてくるメールのリンクを踏んで認証する

すでに持っていれば飛ばしてください。

---

## Step 2. 置き場所（リポジトリ）を作る

1. ログインした状態で [github.com/new](https://github.com/new) を開く
2. **Repository name** に `blog` と入力
3. **Public** を選ぶ（Private だと無料では公開できません）
4. 下の「Add a README file」などのチェックは**すべて外したまま**
5. **Create repository** を押す

次の画面に出てくる `https://github.com/ユーザー名/blog.git` を控えておきます。

---

## Step 3. パソコンから送る

ターミナルで、`ユーザー名` を自分のものに置き換えて実行します。

```bash
cd ~/Desktop/blog
git remote add origin https://github.com/ユーザー名/blog.git
git push -u origin main
```

初回はログインを求められます。

- ブラウザが開いて認証できる場合はそのまま進めてください
- パスワードを聞かれた場合、**GitHubのパスワードでは通りません**。
  [github.com/settings/tokens](https://github.com/settings/tokens) →
  Generate new token (classic) → Note に `blog`、Expiration は `No expiration`、
  **repo にチェック** → Generate token。
  表示された文字列をコピーして、パスワード欄に貼り付けます（画面には何も出ませんがそれで正常）。
  この文字列は二度と表示されないので、メモアプリなどに控えておいてください。

`git push` が通れば、GitHub のページを再読み込みするとファイルが並んでいます。

---

## Step 4. 公開を有効にする

1. GitHubで自分の `blog` リポジトリを開く
2. 上部の **Settings**（歯車）を押す
3. 左の一覧から **Pages** を押す
4. **Build and deployment** の **Source** で `Deploy from a branch` を選ぶ
5. **Branch** を `main`、右の folder を **`/docs`** にして **Save**

数分待ってページを再読み込みすると、上部に公開URLが出ます。

```
https://ユーザー名.github.io/blog/
```

開いてサイトが出れば公開完了です。

<details>
<summary>404 が出るとき</summary>

- 5分ほど待ってから再読み込みする（初回は時間がかかります）
- folder が `/docs` になっているか確認する（`/(root)` だと出ません）
- `docs/index.html` が GitHub 上に存在するか確認する
</details>

---

## Step 5. URLを設定に書く（RSS用）

公開URLが決まったら、`build.py` の10行目あたりを書き換えます。

```python
BASE_URL = "https://ユーザー名.github.io/blog"
```

保存したら `./publish.sh` を実行。これで反映されます。

---

## Step 6. 毎日の流れ

以降はこれだけです。

```bash
cd ~/Desktop/blog
python3 new.py "今日のタイトル"    # 書く
./publish.sh                       # 公開
```

`python3 new.py` を打つとファイルができてエディタが開くので、そのまま書いて保存。
`./publish.sh` を打つと1〜2分で反映されます。

書く前に見た目を確認したいときだけ `python3 build.py --serve` を挟んでください。

<details>
<summary>毎回 cd を打つのが面倒なとき</summary>

`~/.zshrc` に次の1行を足すと、`blog` とだけ打てば移動できます。

```bash
alias blog='cd ~/Desktop/blog'
```

追加後、ターミナルを開き直してください。
</details>

---

## つまずいたら

| 症状 | 対処 |
|---|---|
| `permission denied: ./publish.sh` | `chmod +x publish.sh` を1回実行 |
| `git push` で拒否される | トークンの期限切れ。Step 3 の手順で作り直す |
| 記事が出てこない | `draft: true` になっていないか確認 |
| 公開ページが古いまま | 反映に数分かかります。それでも古いなら `./publish.sh` の出力にエラーが出ていないか確認 |
| `[[ ]]` が点線になる | 書名・記事名が正確か、本を `books/` に登録したか確認 |

書き方や設定項目は `README.md` にまとめてあります。
