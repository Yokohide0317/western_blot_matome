# 開発ポリシー（AIエージェント・開発者向け）

このリポジトリは公開されています。利用者の研究データを扱うツールなので、**研究内容が少しでも分かるものは、リポジトリ・コミット・PR・Issue・Releaseに一切載せない**ことを最優先にします。

## 1. 公開しないもの（コード、README、docs、テスト、コミットメッセージ、PR本文、Issue、コメントのすべてで）

- **画像そのもの**：ChemiDocの書き出し（`.tif` `.tiff` `.scn`）、ZIP、ブロットや膜の写真、切り出し、スクリーンショット、画面キャプチャ、PNG・PPTXの書き出し結果。
- **画像やデータから読み取った内容**
  - 膜や画像に書かれた・写っている分子名、抗体名、タグ名、略号（手書きメモを含む）
  - レーン構成（何レーンをどう並べたか）、サンプル名、条件、使わなかった膜などの実験設計
  - 実データのファイル名・フォルダ名・パス・撮影日時・露光時間の組み合わせ
- **利用者とのやりとりの内容**：会話で知った実験の情報。例文に流用しない。
- **実データ由来のファイル**：`.wblot`、`wblot_output/`、実データから作ったJSON（座標や撮影IDも含め、新しく足さない）。

### 書いてよいもの

- 一般的なローディングコントロールなど、研究内容を特定しない名前：Actin（β-actin）、Hsp90、GAPDH、α/β-Tubulin、Vinculin、Lamin B1 など。
- 例やテストでは架空の名前を使う：`Protein A`、`TargetA`、`Sample 1`、`~/Desktop/blot/ChemiDoc_export.zip`、日付は `2000-01-01`。
- テスト画像は合成する（`tests/fixtures/*/generate.py`）。実データは使わない。

### 実データで確かめるとき

- 実データはリポジトリの外、またはgit管理外の `private/` と `instructions/` に置く（`.gitignore` 済み）。
- 実データを使うテストは環境変数で場所を渡す（`WB_INPUT_ZIP`、`WB_INPUT_ZIP_2`）。無ければskipされる。
- 実データで見つけた不具合を直すときも、テストと説明は合成データと架空の名前で書き直す。

## 2. コミット・pushの前に必ず

```bash
uv run python dev/check_public.py            # 追跡中・ステージ中のファイルを検査
uv run python dev/check_public.py --since origin/main   # pushするコミットのメッセージも検査
```

- 画像・データ形式のファイル（許可したアイコン以外）や大きなファイルがあると失敗します。CIでも実行されます。
- 自分の研究で使う分子名などを `dev/private_terms.txt`（1行に1語、git管理外）に書いておくと、それらが含まれていないかも調べます。CIはこのファイルを持たないので、**pushする本人の環境での実行が本番**です。
- `git add -A` の前に `git status` で新しいファイルを確認する。画像やデータが混ざっていたら追加しない。
- 万一pushしてしまったら、追加のコミットで消しても履歴に残る。すぐ作業を止めて管理者に知らせる（リポジトリの非公開化・作り直しが必要になる）。

## 3. その他の開発ルール

- **コミット・push・タグ・Releaseは、管理者に頼まれたときだけ**行う。
- **画面（`src/wb/web/app`）を変えたら** `npm run build` し、`dist/` もコミットする（配布物はビルド済みの `dist/` を使う）。
- **Skillは2か所**（`skills/wblot/SKILL.md` と `src/wb/skills/wblot/SKILL.md`）を同じ内容に保つ（テストで確認）。
- **WindowsとmacOSの両方で動かす**：
  - テキストの読み書きは `encoding="utf-8"` を明示する。
  - `fcntl`、`os.kill(pid, 0)`（Windowsではプロセスを終了させてしまう）、ソケットのfd受け渡しは直接使わない。`wb.locks.file_lock` や `wb.server` の仕組みを使う。
  - パスは `pathlib` で組み立て、`/` 区切りを文字列で決め打ちしない。
- **利用者のフォルダを汚さない**：データの隣に置くのは `.wblot` と `wblot_output/exports` だけ。キャッシュや状態は `wb.project.cache_dir()` / `app_home()` を使う。テストは `WBM_HOME` / `WBM_CACHE` / `WBM_LIBRARY` / `WBM_CONFIG` を一時フォルダに向ける（`tests/conftest.py` で自動）。
- **テスト**：`uv run pytest` と `cd src/wb/web/app && npm test`。
- **リリース**：`pyproject.toml` の `version` を上げ、同じ番号のタグ（`v0.2.0` など）をpushすると、GitHub ActionsがmacOS・Windowsでテストしてから Release を作る。
- 画面の文言は日本語で、短く具体的に。
