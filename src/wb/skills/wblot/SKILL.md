---
name: wblot
description: Western blot・ウェスタンブロットの図をChemiDocのZIPや画像フォルダ、.wblotから作成・調整・出力する。切り出し範囲、露光、角度、ラベルの変更とブラウザでの確認に使う。
---

# wblot

このSkillは Western Blot Matome（`wbm`）のCLIを使う。プロジェクトファイル（`.wblot`）とSkill ID（`wblot`）は従来どおりの名前。

## 0. 導入確認（毎回最初に）

1. `wbm --version` を実行する。見つからなければ、macOS / Linux は `~/.local/bin/wbm --version`、Windows は `%USERPROFILE%\.local\bin\wbm.exe --version` を試す。アプリから実行するシェルでは `~/.local/bin` がPATHに無いことがあるので、見つかった絶対パスを以後も使う。
2. どれも無ければ、利用者に「Western Blot Matomeを導入します」と伝えて了承を得てから、次の1行を実行する（Xcode Command Line Tools・Python・git・Node.jsは不要。uvが無ければ自動で入る）。
   - macOS / Linux: `curl -LsSf https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.sh | sh`
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.ps1 | iex"`
3. 導入済みなら `wbm update --check --json` で新しいバージョンを確認する（GitHubに問い合わせる。利用者が設定で確認を止めていれば `check` が false で、何もしない）。`available` が true なら「新しいバージョン v<latest> があります（現在 v<current>）」と伝え、了承を得てから `wbm update --json` を実行する。更新は裏で進み、サーバーは止まって更新後に起動し直す。1分ほど待ってから `wbm --version` で新しい番号になったことを確かめる（まだ古ければもう少し待つ。記録は戻り値の `log`）。`can_apply` が false なら `reason` を伝えて判断を任せる（uvで入れた通常の導入なら、上の1行＝戻り値の `command` を実行し直しても更新できる）。
4. `wbm install-skill` はこのSkillを `~/.claude/skills`・`~/.codex/skills`・`~/.agents/skills` に入れる（`--claude`／`--codex`／`--agents` で個別指定）。

Claudeアプリの「Code」タブでは、データ（ZIPやフォルダ）がセッションのフォルダ外にあると読み書きの許可を求められる。必要なら最初にデータのフォルダをセッションに追加するよう利用者に頼む。

## 1. ファイル名の読み方（ラボごとに違う場合）

ChemiDocの書き出しファイル名はラボによって違うことがある。既定は「… 0.200s(Colorimetric).tif」「… 60.000s(Chemiluminescence).raw16.tif」の形。

1. 初めてのデータは、取り込む前に `wbm config --check "<ZIPまたはフォルダ>" --json` を実行する。`signal`（発光）と `marker`（マーカー）の件数、`unrecognised`（読めなかった画像）を確認する。`wbm init` 後に撮影（露光）が1件も無い場合も同じ確認をする。
2. 読めない画像があれば、ファイル名の例を利用者に見せて、どれが発光・マーカーか、露光時間がどこに書かれているかを確かめる。
3. `wbm config --init --json` で説明つきの設定ファイルを作り（既にあれば変更しない）、戻り値の `path` の `[naming]` を書き換える。`wbm config --json` で現在値と場所を確認できる。
   - `pattern`: 拡張子と `raw16_suffix` を除いたファイル名に当てる正規表現。`(?P<exposure>…)`（露光の数値）と `(?P<mode>…)`（撮影モード名）が必須。`(?P<stem>…)` があると `<stem>.scn` を対応づける
   - `exposure_scale`: 露光の数値を秒に直す倍率（ミリ秒なら `0.001`）
   - `signal_modes` / `marker_modes`: 発光・マーカーとみなすモード名（大文字小文字は区別しない）
   - `raw16_suffix`: 16-bit原画像の拡張子直前の目印（既定 `.raw16`）
4. もう一度 `wbm config --check` で全画像が読めることを確かめてから取り込む。取り込み済みのデータは `wbm init "<フォルダ>" --refresh` で読み直す（切り出しなどの編集は初期化される）。
5. 設定ファイルの場所は macOS `~/Library/Application Support/WBM/config.toml`、Windows `%LOCALAPPDATA%\WBM\config.toml`（環境変数 `WBM_CONFIG` で変更可）。セッションのフォルダ外なので、編集の許可を求められることがある。

## 基本作業フロー

1. **プロジェクト初期化と初期提案の適用**:
   ChemiDocのZIPか画像フォルダを受け取ったら、`wbm init "<ZIPまたはフォルダ>" --json` を実行する。
   戻り値の `path` を以後の `<project>` に使い、`wbm propose --apply --project "<project>" --json` で初期提案を反映する。
   **膜が1枚も見つからない、または足りないとき**（`propose --apply` の `panels` が空で `next` が付いているか、写っている膜より少ない）は、利用者に頼まず、自分で画像を見て膜を追加する。
   1. `wbm overview --project "<project>" --json` を実行し、戻り値の `path` の画像を開いて見る。座標の目盛り（単位は元画像のピクセル）付きで画像全体が写っている。緑の枠が登録済みの膜、オレンジの枠がSelection（図に使う範囲）。目盛りが粗ければ `--grid 50` で細かくする。
   2. 写っている膜ごとに、四角い範囲（左上 `x0 y0`、右下 `x1 y1`）と傾きを読み取る。傾きは、バンドの並びが右上がりなら正、右下がりなら負の角度（度）にする。膜の縁が見えにくいときは、バンドの並び（とマーカーのラダー）が収まる範囲を膜とみなす。発光の画像で縁が見えないときは、戻り値の `frames` にあるマーカー画像を `--frame <frame_id>` で見て形をつかんでよい。ただし座標は、必ず既定の基準画像（`is_reference` が true の画像）から読む。
   3. 上の膜から順に `wbm add-membrane <x0> <y0> <x1> <y1> --rotation <角度> --frame <基準画像のframe_id> --project "<project>" --json` で追加する。
   4. もう一度 `wbm overview` を実行して、枠が膜とバンドに合っているかを自分の目で確かめる。ずれていれば `wbm adjust <段> --rotation/--crop-top/--crop-bottom/--crop-left/--crop-right` で直す（切り出しの座標は傾きを補正した座標だが、傾きが小さければ目盛りの値とほぼ同じ）。
   5. 利用者には「自動では膜が見つからなかったので、画像を見て膜を◯枚追加しました。画面（<url>）で範囲が合っているか確かめてください」と短く伝える。
   使わない膜があるかは利用者が決める。すべての膜を追加しておき、除外は指示を受けてから行う。

2. **状態確認**:
   `wbm status --project "<project>" --json` でSelection一覧、採用露光、警告を読み、要点を利用者に伝える。
3. **ブラウザ確認の依頼**:
   `wbm open "<project>" --no-browser --json` を実行し、戻り値の `url` をアプリ内のブラウザパネルで開く（ブラウザパネルが無ければ `--no-browser` を付けずに実行し、既定のブラウザで開く）。そのうえで「ブラウザを開きました（<url>）。1. メンブレン選択 → 2. バンド選択 の順に各段の位置・露光・レーン配置をご確認ください」と、戻り値の `url` をそのまま書いて依頼する。127.0.0.1 の共有サーバー（既定ポート8765）が1つだけ起動し、戻り値の `url`（`http://127.0.0.1:<port>/p/<ID>/`）がそのプロジェクトの画面になる。別のプロジェクトを開いても同じサーバーに別IDで追加される。サーバーのトップ（`hub_url`）と `wbm recent --json` で最近開いたプロジェクトを確認できる。利用者から渡されたファイルは、Web UIにアップロードせず、そのパスで `wbm open` する（結果がデータの隣に保存されるため）。
4. **利用者の指示をCLIで反映**:
   Selectionの手動追加は「2. バンド選択」の＋からドラッグする。行の複製アイコンは範囲・露光をそのまま複製し、×は削除する。段の候補を選ぶ機能はない。メンブレンのON/OFFはSelectionのチェックと独立している。
   利用者の指示（段の追加・変更、角度、ラベル名、露光、並び順、除外、レーン数・試料名など）を以下のコマンドで反映する。画面側は変更を自動で検知して即座に再描画する。
   - ラベル（抗体名等）を変える: `wbm rename <段> <名前> --project "<project>" --json`
   - 採用露光を変える: `wbm set-exposure <段> <撮影ID> --project "<project>" --json`
   - 角度・4辺切り出し範囲・出力幅を直す: `wbm adjust <段> [--rotation D] [--crop-left X0] [--crop-right X1] [--crop-top Y0] [--crop-bottom Y1] [--width PX] --project "<project>" --json`
   - 手動で新しいメンブレン／セレクションを追加する: `wbm add-membrane <x0> <y0> <x1> <y1> [--rotation D] [--membrane-label M] [--label L] [--frame F] --project "<project>" --json`（画面上の一覧下にある `＋` でも追加可）
   - 座標の目盛り付きで画像全体を見る（膜の位置の読み取り・確認用）: `wbm overview [--frame F] [--grid PX] [--raw] --project "<project>" --json`（戻り値の `path` の画像を開く）
   - 段の並び順を変える: `wbm reorder <段1> <段2> ... --project "<project>" --json`（カンマ区切り `A,B,C` も可）
   - 段を図に含める／除外する: `wbm include <段> --project "<project>" --json` / `wbm exclude <段> --project "<project>" --json`
   - 段を複製する（同一膜の別バンド用）: `wbm duplicate <段> --project "<project>" --json`
   - レーン数・試料名を設定する: `wbm lanes <レーン数> [--panel <段>] [--samples 試料1,試料2,...] --project "<project>" --json`（画面上では各段のレーン境界ガイド線をドラッグして幅も調整できる）
   - マーカーを左右にトリミング・移動する（別のラダーに合わせる）: `wbm marker <段> [--left X0] [--right X1] [--shift D] --project "<project>" --json`（座標は切り出しと同じ傾き補正後の座標。`--reset` で自動に戻す）。マーカーの縦範囲は常にバンドの切り出しと同じで、縦を変えるときは `wbm adjust --crop-top/--crop-bottom` でバンドと一緒に変える。画面では「2. バンド選択」でマーカー表示にし、オレンジ枠をドラッグする（上下のハンドルはバンドと連動）。元画像の表示ボタンは、黒丸＝発光、虹色の丸＝マーカー、両方の丸＝Merge（重ね合わせ）。全段のレーン数は「Master Control」で一括変更する
5. **ファイル出力**:
   下書き・確定の区別や承認操作はない。現在の保存内容をそのまま出力する。Colorimetricマーカーが利用可能なら、Selectionと同じ高さで取得して表示・出力する。
   - `wbm export pptx --project "<project>" --json`（PNGなら `wbm export png ...`）
   - PPTXのファイル名は `<root>_<タイムスタンプ>.pptx` の一意名で、既存出力は上書きされない。PPTXではブロット右側に、登録Colorimetricマーカーが画面・PNGと同じ範囲・バンドと同じ高さの1枚の画像として入る（Colorimetric画像の外にはみ出す場合は入れず、画素は捏造しない）。
6. **サーバー停止**:
   作業完了後に止めたい場合は `wbm stop --json`（開いている全プロジェクトの画面が閉じる）。止めなくても、2時間アクセスがなければ自動で停止する。

## 注意事項とルール

- **抗体名・タンパク質名・試料名・レーン数を利用者に尋ねない。** 必要だと思えば、利用者が自分で画面に入力するか、指示してくる。言われたときだけ反映し、それまでラベルは仮の `A`、`B` のままでよい。
- **作業の区切りで返答するときは、必ず画面のURL（`http://127.0.0.1:<port>/p/<ID>/`）を書く。** 分からなければ `wbm open "<project>" --no-browser --json` の `url` を使う。
- `<段>` はパネルラベル（`A`, `B` 等）、`panel_id`、またはSelectionが1つだけのメンブレン名（`M1` 等）で指定する。
- `--project` を省略するとカレントディレクトリの `.wblot` を自動検出する。
- 原画像は非破壊であり、成果物は画像フォルダ内の `wblot_output/` に出力される。
- 「iCloud Driveにだけあり、このMacにダウンロードされていない」と出たら、利用者にFinderでそのフォルダを右クリック→「今すぐダウンロード」してもらってから、同じコマンドをやり直す。
- 8-bit RGB TIFFは閲覧・図版専用（`presentation_only`）であり、定量解析には用いない。露光ストリップの赤ドット `●` は飽和画素を含む警告である。
- レーン数は検出バンド数から自動確定せず、利用者またはExcelメタデータに従う。

## 変更コマンドの簡潔な `--json` レシート仕様

変更系コマンドの `--json` は、AI Agentが巨大な `.wblot` 全体を再読み込みせずに済むよう、更新箇所の最小限のレシートを返す。

- **単一パネル変更** (`set-exposure`, `adjust`, `add-membrane`, `rename`, `include`, `exclude`, `duplicate`):
  `{"ok": true, "project_revision": <整数>, "panel": <更新された段の要約>}`
  （`duplicate` の場合は `"new_panel_id": "..."` も含む）
  ※ `panel` には `panel_id`, `label`, `included`, `display_order`, `lane_count`, `membrane_label`, `region_id`, `review_status`, `selected_band_row_candidate_id`, `selected_exposure_id`, `exposure_s` 等が含まれる。
- **複数パネル変更** (`reorder`, `lanes`):
  `{"ok": true, "project_revision": <整数>, "panels": [<各段の要約>, ...]}`
- **提案反映** (`propose --apply`):
  `{"ok": true, "project_revision": <整数>, "panels": [...], "excluded_image_sets": [...], "warnings_count": <件数>}`
- **提案要約** (`propose --summary --json`):
  プロジェクトを変更せず、提案される段一覧・除外系列・警告件数のみを返す。
