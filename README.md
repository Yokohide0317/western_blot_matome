<img src="docs/icon.png" alt="" width="72" align="right">

# Western Blot Matome (wbm)

ChemiDocのウェスタンブロット画像から、論文・発表用の図（編集できるPowerPointと確認用PNG）を作るツールです。Claude Code や Codex に頼むと、ブラウザの画面を開いて一緒に仕上げます。

https://github.com/user-attachments/assets/382d17b3-be22-44fa-8921-54f770501dc4

動画の画像は、すべて架空のデモデータです。

## 利用上の注意

- このツールは手元のPC上（`127.0.0.1`）で動きます。画像やデータを、このリポジトリの管理者のサーバーなど外部へ送ったり保存したりすることはありません。
- 新しいバージョンがあるかを、1日1回 GitHub に確認します（送るのは確認の要求だけで、画像やデータは送りません）。止めるときは `wbm config --init` で設定ファイル（`config.toml`）を作り、`[update]` の `check = false` にします。
- ただし、Claude・Codex などのAIエージェントと一緒に使うと、作業の過程で画像、ファイル名、会話の内容がAIの提供元へ送信されます。提供元の設定や規約によっては、保存されたり学習に使われたりする場合があります。各提供元のプライバシーポリシーとデータの設定を確認してからご利用ください。
- 図の内容（切り出し範囲、露光、ラベルなど）は、必ず元の画像と照らし合わせて確認してください。
- 本ツールは無保証で提供します。利用によって生じたいかなる損害や不利益についても、リポジトリの管理者およびコントリビューターは責任を負いません。ご自身の責任でご利用ください。

## インストール

Claudeアプリの「Code」タブ（または Claude Code / Codex）で、次のように頼みます。

```text
https://github.com/Yokohide0317/western_blot_matome のREADMEを読んで、ツールとSkillを導入して
```

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

Xcode Command Line Tools・Python・git・Node.js の事前導入は不要です（[uv](https://docs.astral.sh/uv/) が無ければ自動で入り、Pythonも管理します）。インストーラーは本体と起動アプリを配置します。AI用のSkillは別途、次を実行して導入します。

```bash
wbm install-skill
```

Skillの保存先:

- Claude Code: `~/.claude/skills/wblot`
- Codex: `~/.codex/skills/wblot`、`~/.agents/skills/wblot`

更新は `wbm update`（確認だけなら `wbm update --check`）か、同じ1行をもう一度実行します。Skillだけを入れ直すときは `wbm install-skill`（`--claude` / `--codex` / `--agents` で個別に指定）を実行します。

</details>

## AIエージェントへ

<details>
<summary>AIエージェント向けの導入手順（利用者は読まなくて大丈夫です）</summary>

> このセクションは、利用者に「READMEを読んで、ツールとSkillを導入して」と頼まれたAIエージェント（Claude Code、Codex など）への指示です。

利用者はコマンドに慣れていない研究者の場合があります。説明は短く日本語で、専門用語を避けてください。

1. **導入内容を伝える。** WBM本体と起動アプリを配置し、AI用Skillは別途導入することを短く伝えます。利用者から導入を依頼されていれば、同じ承認を繰り返し求めません。画像はローカルで処理されますが、AIと使うと画像・会話がAI提供元へ送られる点は上の「利用上の注意」を案内します。
2. **本体を確認・導入する。** `wbm --version` を実行します。PATHに無ければmacOSは `~/.local/bin/wbm`、Windowsは `%USERPROFILE%\.local\bin\wbm.exe` を使います。未導入なら手順上部のOS別インストーラーを実行します。Xcode・Node.js・git・Pythonを別途用意する必要はありません。通常導入にclone、pip、sudoは使いません。
3. **アプリをApplications / Programsへ配置する。** `wbm install-app --json` を使います。macOSは `/Applications/WBM.app`、書き込み権限がなければ `~/Applications/WBM.app`。Windowsは `%LOCALAPPDATA%\Programs\WBM\WBM.exe`。インストーラーで作成済みなら再作成は不要です。ダウンロード済みの.appを使う場合もApplicationsへ移動し、ダウンロード先から常用しません。管理者権限は追加しません。
4. **Skillを別途導入する。** `wbm install-skill` を実行し、`~/.claude/skills/wblot/SKILL.md`・`~/.codex/skills/wblot/SKILL.md`・`~/.agents/skills/wblot/SKILL.md` を確認します。アプリだけの利用には不要です。本体の更新後にSkillも更新する場合は、このコマンドを実行し直します。
5. **アプリ経由で起動を確認する。** `wbm app --no-browser --json` を実行し、返された `app_pid`・`pid`・`url` を確認します。もう一度実行して同じPIDを返すこと、`wbm config` が動くことも確かめます。既存アプリ・サーバーを再利用するため、Agentが別サーバーを起動する必要はありません。`wbm open "<project>" --no-browser --json` も同じアプリを使います。エラー時に別のPythonサーバーを立てて回避しません。
6. **使い方を伝える。** アプリが開いている間はサーバーが動き、アプリを終了すると全プロジェクトのサーバーが停止します。普段はアプリを開くか、データの場所と「ウェスタンの図にまとめて」をAIに伝えます。Skill導入後に読み込まれなければ新しい会話を始めます。

利用者のデータを、導入の確認のために開いたり送ったりしないでください。

</details>

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

PPTXの2枚目には、切り出す前の全体を発光とマーカーで重ねた「Merge」画像が入ります。露光は、すべての段でバンドがはっきり見えるものを自動で選びます（一部のバンドが少し赤く＝飽和することがあります）。

エージェントを使わずに開くときは、`wbm open "<ZIPまたはフォルダ>"` を実行するか、`http://127.0.0.1:8765/` の「ファイルを開く」「フォルダを開く」から選びます。フォルダは、1つ下のサブフォルダの画像まで読み込みます（撮影ごとにフォルダが分かれていても、同じ膜の露光としてまとめます）。

画面上部の「元に戻す」「やり直す」で、枠の移動・ラベル・露光・削除・定量設定など、保存済みの編集を最大50操作まで戻せます。Ctrl／⌘＋Zで取り消し、Ctrl／⌘＋Shift＋Z（WindowsではCtrl＋Yも可）でやり直せます。入力欄では文字入力の取り消しを優先します。履歴はローカルに保持され、画面を再読み込みしても使えます。取り消した後に新しい編集を行うと、やり直しの履歴は消えます。

## 定量（任意）

図の作成とは別に、同じ幅・高さの四角い枠（ROI）でバンドの強さを測れます。画面の「3. 定量(Optional)」で操作し、使わなくてもPPTX・PNGの書き出しはそのまま完了できます。

1. 段を選び、「定量を開始」を押します。バンド選択の範囲とレーン配置を使って、バンド枠と近くの背景枠を仮配置します。
2. 定量には16-bitグレースケール元画像（raw16）を使います。raw16がない場合はその旨が表示され、定量できません。表示用TIFFは代用できません。
3. 画像を見ながら、バンドの枠と近くの背景の枠を合わせます。枠の内側をドラッグすると、その枠だけを移動できます。選択した枠の角・辺をドラッグすると、全レーンのバンド枠・背景枠を、各中心位置を固定して一括で伸縮できます（2px刻み）。見づらい場合は「白黒反転」で表示を切り替えられます。
4. 各定量グループを「計算」で保存し、「Excel（全件）」で一度にダウンロードします。段・定量グループごとに結果を別シートに分け、共通の「解析条件」シートを付けた1つのExcelになります。未計算・再計算が必要なグループがある場合は、全件を計算してから出力してください。CSVは現在選んでいるグループの出力です。

- 信号の向き・飽和しきい値は「計算設定」で変更できます。バンド選択を変更した後は、同じ欄の「バンド選択から枠を再配置」で反映できます（現在の枠と計算結果を置き換えます）。
- 自動配置は初期位置の提案です。表示画像とraw16の倍率を寸法から推定した場合は、その旨を表示します。対応が分からない場合は仮の等間隔配置になるため、画像上でバンドと背景を合わせてください。
- 値は元画像の画素値から計算します。画面の白黒反転・明るさ・コントラスト・拡大率を変えても値は変わりません。
- 背景を差し引いた値が負になる場合も、ゼロに丸めずそのまま残します。飽和したレーンは有効な値を出しません。
- 現在は、ローディングコントロールによる正規化・基準レーンに対する相対値には対応していません。
- 比較するレーンは、同じ膜・同じ露光の画像で揃えてください。

### コマンドで定量する

```bash
wbm quantify start A --project "<project>" --json
wbm quantify show --project "<project>" --json
wbm quantify show <group_id> --project "<project>" --json
wbm quantify place <group_id> --project "<project>" --json
wbm quantify update <group_id> --settings settings.json --project "<project>" --json
wbm quantify compute <group_id> --project "<project>" --json
wbm quantify export <group_id> --format excel --project "<project>" --json
wbm quantify export --all --project "<project>" --json
```

`start` はバンド選択から新しい定量グループを作り、`group.group_id` と配置の説明を返します。`--capture <撮影ID>` で露光を指定できます。`place` は現在のバンド選択から既存グループを再配置します。計算は `compute` を実行したときだけ行います。`export --all` は全グループを別シートにまとめて1つのExcelで出力します。単独グループの `export <group_id>` は `--format csv` にも対応し、`wblot_output/exports/` 内の新しいファイルのパスを返します。

`update` のJSONには、変更する `signal_polarity`（`high` / `low`）、`saturation_value`（整数または `null`）、`width_px`・`height_px`、`lanes` を指定します。`lanes` は `show` が返す全レーンのリストを使い、`x`・`y`・`background_x`・`background_y`・`excluded` を変更できます。座標はraw16元画像のpxです。サイズ変更は全枠の中心を保つ2px刻みです。

## macOS / Windowsの常駐アプリ

`wbm install-app --json` で起動アプリを配置できます。OS別インストーラーも同じ処理を行います。

| OS | アプリの配置先 |
| --- | --- |
| macOS | `/Applications/WBM.app`（書き込めない場合は `~/Applications/WBM.app`） |
| Windows | `%LOCALAPPDATA%\Programs\WBM\WBM.exe` |

アプリを開くと共通サーバーとホーム画面が開きます。ブラウザを閉じてもアプリが動いている間は常駐し、アプリを終了するとサーバーも止まります。macOSはDockのWBMから画面を開き直せます。WindowsはスタートメニューのWBM、または配置先のWBM.exeから開き、WBMウィンドウの「画面を開く」「終了」を使います。

AIやコマンドからは `wbm app --no-browser --json` で起動します。既存のアプリとサーバーがあれば同じものを使います。`wbm open` と `wbm start` もmacOS/Windowsではアプリ経由です。先にCLIで起動していたサーバーもアプリが引き継ぐため、プロジェクトごとにサーバーを増やす必要はありません。

アプリは導入済みのwbmを使用するランチャーです。別のPCでは、そのPCに本体をインストールしてから作成してください。Skillはアプリと別の `wbm install-skill` で導入します。GUIのないCIなどで直接サーバーを使う場合だけ `WBM_HEADLESS=1` を設定します（Linuxでは通常のCLIサーバーを利用）。

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
wbm install-app
wbm install-skill
```

編集可能な開発環境からアプリを作る場合、実行環境はローカルのアプリデータ領域へコピーされます。ソース変更をアプリに反映するときは、アプリを終了して `wbm install-app` を実行し直します。

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
wbm open                                      # アプリ経由で画面を開く（共通サーバーを再利用）
wbm recent                                    # 最近開いたプロジェクト
wbm stop                                      # 共通サーバーを明示的に停止（通常はアプリを終了）
wbm app --no-browser --json                    # AI用: アプリを起動・再利用してPIDとURLを取得
wbm start                                     # 互換コマンド: アプリ経由で起動
wbm update --check                            # 新しいバージョンを確認（wbm update で更新）

wbm rename A "Protein A"                      # ラベル
wbm set-exposure A CAP_IMG049                 # 採用露光
wbm adjust A --rotation -1.2 --crop-top 410 --crop-bottom 495   # 角度・切り出し
wbm add-membrane 200 180 960 540 --rotation 0.5 --label "ACTB"  # 膜を手動追加
wbm overview                                  # 座標の目盛り付きの全体画像（膜の位置を読む・確かめる）
wbm lanes 6 --samples WT,KO,Rescue,Ctrl1,Ctrl2,Ctrl3            # レーン数・試料名
wbm marker A --shift 40                       # マーカーを横に移動（--left/--right、--reset）
wbm reorder B A                               # 並べ替え
wbm exclude C                                 # 図から除外
wbm duplicate A                               # 段を複製

wbm export pptx                               # PowerPoint（2枚目に全体のMerge画像。--no-merge で省く）
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
