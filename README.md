<img src="docs/icon.png" alt="" width="72" align="right">

# Western Blot Matome (wbm)

ChemiDocのウェスタンブロット画像から、論文・発表用の図（編集できるPowerPointと確認用PNG）を作るツールです。Claude Code や Codex に頼むと、ブラウザの画面を開いて一緒に仕上げます。

## 利用上の注意

- このツールは手元のPC上（`127.0.0.1`）で動きます。画像やデータを、このリポジトリの管理者のサーバーなど外部へ送ったり保存したりすることはありません。
- ただし、Claude・Codex などのAIエージェントと一緒に使うと、作業の過程で画像、ファイル名、会話の内容がAIの提供元へ送信されます。提供元の設定や規約によっては、保存されたり学習に使われたりする場合があります。各提供元のプライバシーポリシーとデータの設定を確認してからご利用ください。
- 図の内容（切り出し範囲、露光、ラベルなど）は、必ず元の画像と照らし合わせて確認してください。
- 本ツールは無保証で提供します。利用によって生じたいかなる損害や不利益についても、リポジトリの管理者およびコントリビューターは責任を負いません。ご自身の責任でご利用ください。

## インストール

Xcode Command Line Tools・Python・git・Node.js は不要です（[uv](https://docs.astral.sh/uv/) が無ければ自動で入ります）。更新も同じ1行です。

macOS:

```bash
curl -LsSf https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.sh | sh
```

Windows（PowerShell）:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.ps1 | iex"
```

## Skillの導入

上のインストールで、AI用のSkill「wblot」も次の場所に入ります。手作業は不要です。

- Claude Code: `~/.claude/skills/wblot`
- Codex: `~/.codex/skills/wblot`、`~/.agents/skills/wblot`

入れ直すときや、片方だけに入れるときは次を実行します。

```bash
wbm install-skill            # すべて
wbm install-skill --claude   # Claude Code だけ（--codex / --agents も可）
```

まだ `wbm` を入れていないPCでも、Skillだけ置いてあれば、エージェントが了承を得てからインストールします。

## 使い方（Claude / Codex への頼み方）

Claudeアプリの「Code」タブ（または Claude Code / Codex）で、データの場所を添えて頼みます。データのフォルダはセッションに追加しておくとスムーズです。

> ~/Desktop/blot/ChemiDoc_export.zip をウェスタンの図にまとめて

エージェントがデータを取り込み、画面（`http://127.0.0.1:8765/p/<ID>/`）を開きます。あとは画面で直すか、言葉で頼みます。

> レーンは6本で、2枚目の膜は使いません
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
