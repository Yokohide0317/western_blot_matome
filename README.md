<img src="docs/icon.png" alt="" width="72" align="right">

# Western Blot Matome (wbm)

ChemiDocのウェスタンブロット画像から、論文・発表用の図（編集できるPowerPointと確認用PNG）を作るツールです。Claude Code や Codex に頼むと、ブラウザの画面を開いて一緒に仕上げます。

## 利用上の注意

- このツールは手元のPC上（`127.0.0.1`）で動きます。画像やデータを、このリポジトリの管理者のサーバーなど外部へ送ったり保存したりすることはありません。
- 新しいバージョンがあるかを、1日1回 GitHub に確認します（送るのは確認の要求だけで、画像やデータは送りません）。止めるときは `wbm config --init` で設定ファイル（`config.toml`）を作り、`[update]` の `check = false` にします。
- ただし、Claude・Codex などのAIエージェントと一緒に使うと、作業の過程で画像、ファイル名、会話の内容がAIの提供元へ送信されます。提供元の設定や規約によっては、保存されたり学習に使われたりする場合があります。各提供元のプライバシーポリシーとデータの設定を確認してからご利用ください。
- 図の内容（切り出し範囲、露光、ラベルなど）は、必ず元の画像と照らし合わせて確認してください。
- 本ツールは無保証で提供します。利用によって生じたいかなる損害や不利益についても、リポジトリの管理者およびコントリビューターは責任を負いません。ご自身の責任でご利用ください。

## インストール

Claudeアプリの「Code」タブ（または Claude Code / Codex）で、次のように頼みます。

> https://github.com/Yokohide0317/western_blot_matome のREADMEを読んで、ツールとSkillを導入して

エージェントが下の「[AIエージェントへ](#aiエージェントへ)」に沿って、ツール（`wbm`）とAI用のSkill「wblot」を入れます。コマンドの実行の許可を求められたら、内容を確かめて許可してください。

<details>
<summary>自分でコマンドを実行して入れる場合</summary>

macOS:

```bash
curl -LsSf https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.sh | sh
```

Windows（PowerShell）:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.ps1 | iex"
```

Xcode Command Line Tools・Python・git・Node.js は不要です（[uv](https://docs.astral.sh/uv/) が無ければ自動で入ります）。Skillも次の場所に一緒に入ります。

- Claude Code: `~/.claude/skills/wblot`
- Codex: `~/.codex/skills/wblot`、`~/.agents/skills/wblot`

更新は `wbm update`（確認だけなら `wbm update --check`）か、同じ1行をもう一度実行します。Skillだけを入れ直すときは `wbm install-skill`（`--claude` / `--codex` / `--agents` で個別に指定）を実行します。

</details>

## AIエージェントへ

> このセクションは、利用者に「READMEを読んで、ツールとSkillを導入して」と頼まれたAIエージェント（Claude Code、Codex など）への指示です。

利用者はコマンドに慣れていない研究者の場合があります。説明は短く日本語で、専門用語を避けてください。

1. **了承を得る。** 次の3点を短く伝え、了承を得てから進めます。
   - 入れるもの：コマンド `wbm` と、AI用のSkill「wblot」（`~/.claude/skills`、`~/.codex/skills`、`~/.agents/skills`）。uv が無ければ uv も入ります。管理者権限は使いません。
   - ツールは手元のPC上で動き、画像やデータを外部のサーバーへ送りません。ただし、AIと一緒に使うと画像や会話の内容がAIの提供元へ送られ、設定によっては保存されることがあります（上の「利用上の注意」）。
   - 新しいバージョンがあるかを1日1回 GitHub に確認します（設定で止められます）。
2. **入っているか確かめる。** `wbm --version` を実行します。見つからなければ、macOS / Linux は `~/.local/bin/wbm --version`、Windows は `%USERPROFILE%\.local\bin\wbm.exe --version` を試します（アプリから実行するシェルでは `~/.local/bin` がPATHに無いことがあります。見つかった絶対パスを以後も使います）。
   - 入っていれば、`wbm update --check --json` で新しいバージョンを確かめます。`available` が true なら、了承を得て `wbm update --json` を実行し、1分ほど待ってから `wbm --version` で番号が変わったことを確かめます。`wbm update` が無い古い版なら、手順3の1行を実行し直せば更新されます。続けて `wbm install-skill` でSkillを入れ直し、手順4へ進みます。
3. **入れる。** OSに合わせて、次の1行だけを実行します。
   - macOS / Linux: `curl -LsSf https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.sh | sh`
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.ps1 | iex"`

   `sudo`、Homebrew、`pip`、リポジトリの clone など、ほかの方法は使いません（clone は開発用です）。失敗したら、表示されたエラーをそのまま短く伝えて止まり、別の方法を試す前に利用者に相談します。ネットワークやプロキシの制限でダウンロードできないことがあります。
4. **確かめる。** 次の3つを確かめます。
   - `wbm --version`（または手順2の絶対パス）がバージョンを表示する。
   - `~/.claude/skills/wblot/SKILL.md` と `~/.codex/skills/wblot/SKILL.md` がある。
   - `wbm config` がエラーにならない。
5. **伝える。** 入ったバージョンと、次の使い方を伝えます。
   - ChemiDocのZIPやフォルダの場所を添えて「ウェスタンの図にまとめて」と頼めば始められる。
   - Skillが読み込まれていないときは、新しいセッション（会話）を始める。
   - データのフォルダをセッションに追加しておくと、読み書きの許可を何度も聞かれずに済む。

利用者のデータを、導入の確認のために開いたり送ったりしないでください。

## 使い方（Claude / Codex への頼み方）

Claudeアプリの「Code」タブ（または Claude Code / Codex）で、データの場所を添えて頼みます。再現性担保のため、作業ファイルを作る仕様になっています。普段、データを置いている場所に設置してから、その場所をツールで指定することをお勧めします（後で移動したり名前を変えたりしない）。

> ~/Desktop/blot/ChemiDoc_export/ で作業。wbmを使って、ウェスタンの図にまとめて。私が確認するので、WebUIを開き、一緒に作業。

エージェントがデータを取り込み、画面（`http://127.0.0.1:8765/p/<ID>/`）を開きます。あとは画面で直すか、言葉で頼みます。

> レーンは6本(2+マーカー+3)で、上から2番目の膜は使いません
>
> 1段目のラベルを Protein A に、2段目は Protein B に
>
> PPTXで書き出して

結果はデータの隣に保存されます（`<フォルダ名>.wblot` と `wblot_output/exports/` のPNG・PPTX）。画面のPPTX・PNGボタンを押すと、同じファイルがダウンロードもされます。

エージェントを使わずに開くときは、`wbm open "<ZIPまたはフォルダ>"` を実行するか、`http://127.0.0.1:8765/` の「ファイルを開く」「フォルダを開く」から選びます。

## ファイル名の設定（ラボごとに違う場合）

既定では、ChemiDoc標準の名前を読みます。

```text
… 0.200s(Colorimetric).tif               マーカー、露光 0.2 秒
… 60.000s(Chemiluminescence).raw16.tif   発光（16-bit原画像）、露光 60 秒
… 60.000s.scn                            撮影条件
```

名前が違う場合は、設定ファイルで読み方を変えます。エージェントに「ファイル名の読み方を合わせて」と頼めば、次の手順をSkillに沿って行います。

```bash
wbm config --check "<ZIPまたはフォルダ>"   # 今の設定でどう読まれるかを表示
wbm config --init                         # 説明つきの設定ファイルを作る
wbm config                                # 場所と現在の値を表示
```

設定ファイルの場所は、macOS が `~/Library/Application Support/WBM/config.toml`、Windows が `%LOCALAPPDATA%\WBM\config.toml` です。例えば `blot1_Chemi_1500ms.tif` のような名前なら、次のように書きます。

```toml
[naming]
pattern = '(?P<stem>.*)_(?P<mode>Chemi|Marker)_(?P<exposure>\d+)ms$'
exposure_scale = 0.001        # ミリ秒 → 秒
signal_modes = ["Chemi"]      # 発光
marker_modes = ["Marker"]     # マーカー
raw16_suffix = "_16bit"       # 16-bit原画像の目印（拡張子の直前）
```

設定は、これから取り込むデータに効きます。取り込み済みのデータは `wbm init "<フォルダ>" --refresh` で読み直します（切り出しなどの編集は初期化されます）。

## 保存場所

| | macOS | Windows |
|---|---|---|
| 作業内容と書き出し | データの隣 | データの隣 |
| 画面にドロップしたZIP | `~/Documents/western_blot_matome/` | `%USERPROFILE%\Documents\western_blot_matome\` |
| 設定・最近開いた一覧 | `~/Library/Application Support/WBM/` | `%LOCALAPPDATA%\WBM\` |
| 描画キャッシュ（削除可） | `~/Library/Caches/WBM/` | `%LOCALAPPDATA%\WBM\Cache\` |

## Development

開発に参加する人とAIエージェントは、最初に [dev/AGENT_POLICY.md](dev/AGENT_POLICY.md) を読んでください。利用者の画像や、そこから読み取った研究内容（分子名・レーン構成・ファイル名など）は、リポジトリに一切載せません。コミットやpushの前に `uv run python dev/check_public.py` を実行します。

### このリポジトリから導入

```bash
uv tool install --force .
wbm install-skill
```

画面（`src/wb/web/app`）を変えたときは、先にビルドします。

```bash
cd src/wb/web/app && npm ci && npm run build
```

### CLI

```bash
wbm init "path/to/ChemiDoc_folder"            # 取り込み（ZIPならZIPの隣に展開）
wbm init "path/to/ChemiDoc_folder" --refresh  # 画像を追加したときなど、取り込み直す（編集は初期化）
wbm propose --apply                           # 初期提案を反映
wbm status                                    # 採用画像と編集状態
wbm open                                      # 画面を開く（共有サーバーを起動）
wbm recent                                    # 最近開いたプロジェクト
wbm stop                                      # サーバーを停止（2時間使わなければ自動停止）
wbm start                                     # サーバーを起動してURLを表示
wbm update --check                            # 新しいバージョンを確認（wbm update で更新）

wbm rename A "Protein A"                      # ラベル
wbm set-exposure A CAP_IMG049                 # 採用露光
wbm adjust A --rotation -1.2 --crop-top 410 --crop-bottom 495   # 角度・切り出し
wbm add-membrane 200 180 960 540 --rotation 0.5 --label "ACTB"  # 膜を手動追加
wbm lanes 6 --samples WT,KO,Rescue,Ctrl1,Ctrl2,Ctrl3            # レーン数・試料名
wbm marker A --shift 40                       # マーカーを横に移動（--left/--right、--reset）
wbm reorder B A                               # 並べ替え
wbm exclude C                                 # 図から除外
wbm duplicate A                               # 段を複製

wbm export pptx                               # PowerPoint
wbm export png                                # PNG
wbm config --check "path/to/ChemiDoc_folder"  # ファイル名の読み方を確認
```

`--project <ZIP/フォルダ/.wblot>` で対象を指定します（省略時はカレントディレクトリ）。`--json` で機械向けの出力になります。

### テスト

```bash
uv run pytest
cd src/wb/web/app && npm test
```

実データで取り込みから出力までを確かめるときは、次を実行します。

```bash
uv run python scripts/check_input.py "path/to/ChemiDoc_folder"
```

### リリース

1. `pyproject.toml` の `version` を上げてコミットする（例: `0.2.0`）。
2. 同じ番号のタグを送る: `git tag v0.2.0 && git push origin v0.2.0`
3. GitHub Actions が macOS / Windows でテストし、wheel・`install.sh`・`install.ps1` を添付したReleaseを作ります。タグとバージョンが違うと止まります。
