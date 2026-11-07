# 実装設計書

本ツール（`wbm`）は、ChemiDoc等のウェスタンブロット撮影画像群から、論文・発表用の図版（編集可能PowerPointおよびPNG）を整理・作成するためのツールである。
仕様の学術的・幾何学的な正本は `instructions/western_blot_implementation_spec.md` であり、本設計書はそれを満たすソフトウェア構造、データ契約、UI/UX設計、CLI・HTTP・Agentインターフェースを定義する。

---

## 1. パッケージ構成と責務境界

Pythonパッケージは `src/wb/` 配下に配置する。依存関係は `pyproject.toml` で管理し、`uv` でインストール・実行する。

| モジュール | 責務と主な機能 | 依存関係 |
| --- | --- | --- |
| `wb/geometry.py` | 外縁座標規約、同次変換行列（`Affine`）、変換合成と逆変換、OpenCV変換アダプター（`geometry.warp`）、回転矩形・多角形計算、マーカー水平スラブ区間計算（`marker_x_span`）、レーン等間隔配置、座標範囲検証 | `numpy`, `cv2` |
| `wb/ingest.py` | 安全なZIP展開、TIFF/SCN台帳化、露光系列候補分類、赤色飽和表示（`R>240, G<20, B<20`）の検出、Excelメタデータ正規化 | `numpy`, `tifffile`, `openpyxl` |
| `wb/model.py` | `.wblot`（プロジェクト状態）のPydanticモデル定義。`Panel.included: bool = True`（図版出力に含めるかのトグルフラグ）を含む | `pydantic` |
| `wb/project.py` | プロジェクト初期化、安定ID生成、版管理（`project_revision`）、アトミック書き込み（`os.replace`）、SHA-256整合性照合、状態遷移 | `model`, `ingest` |
| `wb/propose.py` | 自動レイアウト提案（`proposal_format: 3`）。カラーメトリック画像等からのメンブレン検出、左右両端・カット膜を含むマーカーレーン検出（`detect_marker_lanes`）、傾き・レーン候補推定、1膜1段の初期選択（`row`）と全候補保持（`row_candidates`） | `geometry`, `model`, `rendering` |
| `wb/rendering.py` | 原画像から表示用パネル・マーカー・全体画像の単一パス描画（多重補間防止）、全露光への共通幾何適用、キャッシュ管理 | `geometry`, `model`, `cv2` |
| `wb/layout.py` | 完成図の単一レイアウト計算（Python側で一元管理）。`included=True` の段のみを配置し、ブロットと同一高さのColorimetricマーカー配置を含むインチ単位の座標・画像URL・テキスト情報を算出 | `model`, `geometry` |
| `wb/export.py` | `python-pptx` および Pillow によるファイル書き出し。ブロット画像・マーカー画像と編集可能なテキスト・表を組み合わせたPPTXおよび確認用PNGを出力 | `python-pptx`, `PIL`, `layout`, `rendering` |
| `wb/readiness.py` | 確定版出力可否判定（`output_readiness`）と、ブロッキング理由の日本語対応表（`REASON_TEXT`） | `model` |
| `wb/api.py` | CLI・Web画面・Skillが共有する中核操作API。プロジェクト読み書き、楽観的排他制御、段・手動メンブレン操作、確定時のColorimetricマーカー自動抽出・確定処理 | `model`, `project`, `geometry`, `rendering`, `layout`, `export`, `readiness` |
| `wb/server.py` + `wb/app.py` | 127.0.0.1 のみでリッスンするバックグラウンドFastAPIサーバー。起動中PIDとポートを `wblot_output/server.json` で管理 | `fastapi`, `uvicorn`, `api` |
| `wb/web/app/` | Svelte + Vite によるシングルページGUI。ビルド済み資産（`dist/`）をPythonパッケージ内に同梱し、Node.js環境なしで動作 | なし（静的配信） |
| `wb/cli.py` | コマンドラインインターフェース（`wbm`）。全操作のCLI提供と、AI Agent向けの簡潔な `--json` レシート出力 | `api`, `project` |
| `wb/skills/wblot/SKILL.md` | AI Agent（Claude Code / Codex）用Skillファイル。`skills/wblot/SKILL.md` とバイト単位で完全一致を維持 | なし |

---

## 2. ディレクトリ・ファイル構成

原画像を複製・移動せず、画像が存在するフォルダ内に非破壊で作業成果物を保存する。

```text
<画像フォルダ>/                        # 撮影画像フォルダ（読み取り専用、変更しない）
  *.tif, *.scn, ...                  # ChemiDoc等の生データファイル群
  <フォルダ名>.wblot                  # プロジェクト正本（JSON形式）
  wblot_output/                      # ツール出力先（.gitignore推奨）
    panels/                          # 切り出し済みパネル画像（PNG）
    overviews/                       # 元写真の全体縮小プレビュー（PNG）
    exports/                         # 書き出されたPPTX・PNG
    cache/                           # 再生成可能な中間レンダリングキャッシュ
    server.json                      # バックグラウンドサーバーの状態（PID・ポート）
```

- **ZIP入力時の挙動**: ZIPファイルを指定された場合、ZIPと同名のフォルダを隣に一度だけ安全に展開し、以降はそのフォルダを `<画像フォルダ>` として扱う。
- **相対パスと可搬性**: `.wblot` 内の `sources[].relative_path` は画像フォルダからの相対パスで記録する。画像フォルダごと別PCや別ディレクトリに移動してもそのまま開くことができる。
- **SHA-256照合**: 画像読み込み時にSHA-256ハッシュを検証し、元ファイルが変更されていた場合は既存座標の黙示的な使い回しを拒否する。

---

## 3. 座標とデータの不変条件

### 3.1 外縁座標規約
- **原点と範囲**: 画像の左上外縁を `(0.0, 0.0)` とし、幅 $W$、高さ $H$ の画像の外縁範囲は $[0.0, W] \times [0.0, H]$ とする。
- **画素中心**: 画像配列 `image[row, col]` の画素中心は `(col + 0.5, row + 0.5)` である。
- **浮動小数点数保存**: 座標値・矩形範囲はすべて float で保存し、画面表示用の整数ピクセルに丸めて保存しない。
- **切り出し半開区間**: 切り出しスライスは半開区間 $[x_0, x_1) \times [y_0, y_1)$ として厳密に扱う。

### 3.2 同次変換行列
- 幾何変換は $3 \times 3$ の同次行列（row-major）で保存し、列ベクトル $[x, y, 1]^T$ に左から乗算する。
- 変換レコードの名称は `T_destination_from_source` とし、必ず `from_frame_id` と `to_frame_id` を持つ。
  - $A_e$: 個々の露光画像（`source_frame`）から系列基準画像（`reference_frame`）への位置合わせ行列
  - $D_r$: 基準画像からメンブレン領域 $r$ の傾き補正後座標（`rectified_frame`）への回転剛体変換
  - $C_p$: 補正後座標からパネル切り出し原点への平行移動
  - $S_p$: 等倍率スケーリング（目標幅 $W_{target}$ への等方拡大縮小）
- パネル描画の合成式:
  $$\mathbf{p}_{\text{panel}} = S_p C_p D_r A_e \, \mathbf{p}_{\text{source}}$$
- **単一描画**: パネル描画は合成変換行列を用いて一度の `cv2.warpAffine` で行い、回転とリサイズを繰り返す多重サンプリング・画像劣化を排除する。

### 3.3 OpenCV変換アダプターの局所化
OpenCVは整数画素中心座標系を用いるため、外縁座標系の変換行列 $M_{edge}$ との変換は `geometry.warp` 内でのみ次式により行う。
$$M_{\text{opencv}} = \text{Translate}(-0.5, -0.5) \cdot M_{edge} \cdot \text{Translate}(+0.5, +0.5)$$
コードベース内の他モジュールから直接 `cv2.warpAffine` を呼ぶことを禁止する。

### 3.4 元写真上の切り出し形状
傾き補正後の矩形 crop を元写真（基準画像・露光画像）に逆投影した形状は一般に傾いた4点多角形（凸四角形）となる。軸平行バウンディングボックスで代用せず、多角形（`FramePolygon`）として正しく逆写像・描画する。

### 3.5 科学的データの保全ルール
1. **8-bit RGB TIFFの定量禁止**:
   - 提供される8-bit RGB TIFFは閲覧・図版作成専用（`quant_eligibility: "presentation_only"`）とする。
   - 飽和表示画素（$R > 240, G < 20, B < 20$）を含む場合はUI上で赤ドット `●` による警告を表示する。赤色を他の色で塗りつぶして「生データに戻した」と偽らない。
   - 将来の定量解析には16-bit生データ（SCN等）からの正式な変換経路を用意し、表示用TIFFから強度値を復元しない。
2. **レーン数の自動確定禁止**:
   - 検出バンド数からレーン数を自動確定しない。
   - レーン数と順序はExcelメタデータまたは人間による目視確認によってのみ確定する。
3. **閲覧状態と保存状態の分離**:
   - 比較中のプレビュー露光（`preview`）、拡大率・パン位置（`viewBox`）、選択中パネル（`selectedPanelId`）はブラウザのセッション内状態であり、`.wblot` の正本には記録しない。
   - `.wblot` に保存されるのは明示的に採用された露光（`selected_exposure_id`）および確定された幾何パラメータのみとする。
4. **楽観的排他制御**:
   - `.wblot` 保存時は `expected_revision` を照合し、不一致時は HTTP 409 Conflict を返して上書き破壊を防ぐ。

---

## 4. 1画面・直接操作ワークベンチ UI/UX 設計

旧設計の「3ステップタブ切り替え」「px数値入力フォーム」「常設スライダー」「自由記述の根拠欄」を廃止し、専門家が迷わず直感的に判断できる1画面完結のワークベンチを提供する。

```text
+---------------------------------------------------------------------------------------------------+
| ▤ <project_id>  [3/5 ✓]                 [⟳ 自動提案] [✓ すべて確定] [下書き|確定] [⬇ PPTX] [⬇ PNG] |
+------------------------------------+--------------------------------------------------------------+
| 【左ペイン: 完成図プレビュー】      | 【右ペイン: 元写真 SVG キャンバス】                           |
|                                    |  [M1 · − -1.2° +] [発光|マーカー|重ね] [+ 膜|+ 段] [膜|全体] |
| [− レーン 6 +] [ 1 | 2 | 3 | ... ] |                                                              |
|                                    |       +---------------------------------------------+        |
| ⠿ [TargetA]   [===== =====] [==]     |       |        [ M1 メンブレン領域 ]                |        |
|   ↑ ↓ ✓ + ×                        |       |    +---------------●-----------------+      |        |
|                                    |       |    ▐  === === === (採用中 crop)      ▐──●   |        |
| ⠿ [TargetB ]   [ ---   --- ] [==]     |       |    +---------------●-----------------+      |        |
|   ↑ ↓ ✓ + ×                        |       |                                             |        |
|                                    |       |    + - - - - - - - - - - - - - - - - +      |        |
| 非表示 (1): [+ C]                  |       |    :  (別候補枠: クリックで切替)     :      |        |
|                                    |       +---------------------------------------------+        |
+------------------------------------+--------------------------------------------------------------+
| 【下部ペイン: 露光フィルムストリップ】                                                             |
| [10s ✓] [20s ✓] [40s ✓] [60s ● ✓ (採用中)] [120s ● ✓] [180s ● ✓]                                  |
+---------------------------------------------------------------------------------------------------+
```

### 4.1 主要UIコンポーネント
1. **上部バー（`TopBar.svelte`）**:
   - プロジェクト識別子、確定状況バッジ（`3/5 ✓` または `✓ 確定可`、クリックで未確定項目ポップオーバーを表示）。
   - `⟳ 自動提案`、`✓ すべて確定`、`[下書き | 確定]` モード切替、`⬇ PPTX`、`⬇ PNG` ボタン。
2. **左ペイン2段階タブ（`App.svelte`）**: `1. メンブレン選択` → `2. バンド選択` の段階的ワークフロー。
   - メンブレン段階: メンブレン一覧（ラベル・確定状況）と元写真上のポリゴン選択、`+ 膜` 追加、`次へ バンド選択`。
   - バンド段階: 完成図プレビュー（`FigurePane.svelte` / `FigureRow.svelte`）。全段を表示するが編集可能なのは選択中メンブレンの段のみで、他メンブレンの段は読み取り専用（ガイド線のみ表示）。別メンブレンのポリゴン・段選択・跨ぐ並べ替えは行えない。タブ切替は進行中のドラッグ・描画をキャンセルする。
   - レーン数ステッパー（`− レーン N +`）とレーンごとの試料名インライン入力セル。
   - レーン境界ガイド: 各段のブロット画像上にレーン境界の破線（段ごとの正規化 `lane_layout.boundaries`、未設定時は等間隔フォールバック）。内部境界はドラッグまたは矢印キーで調整し（最小間隔 `min(0.02, 0.5/レーン数)`、25レーン超では自動緩和）、離した時点で `set_lane_boundaries` に保存。レーン数変更時は等間隔にリセット、同数ならカスタム幅を保持。サンプル表のセル幅も選択中の段の境界に合わせて可変になる。
   - 各段のコントロール:
     - `⠿` グリップ（ドラッグ＆ドロップ）および `↑`/`↓` による表示順（`display_order`）の並べ替え。
     - ラベル名のインラインテキスト編集（`Enter` / `blur` で自動保存）。
     - `✓`（段の1クリック目視確定。Colorimetric写真がある場合は同じ高さ `[y0, y1]` のマーカーも自動抽出・確定して写真の右側に同一高さで並べてプレビュー）、`+`（同一膜からの段追加・複製）、`×`（非表示化、`included: false`）。
   - 非表示トレイ（`非表示 (N): [+ ラベル]` の1クリック復元）。
3. **右ペイン（直接操作SVG元写真キャンバス `SourceCanvas.svelte`）**:
   - `[発光 | マーカー | 重ね]` 切替、`[+ 膜 | + 段]` 手動ドラッグ追加モード切替、および `[膜 | 全体]` フィット表示切替（初期表示は選択膜にフィット）。
   - 各メンブレンごとの角度手動調整：上部ピル内の `[− | 0.0° | +]` ボタン（クリックで微調整スライダー＆ `0°` リセットポップオーバーが開き、全表示モードで利用可能）およびキャンバス上の右回転ハンドルドラッグ。
   - 上端・下端・左端・右端ハンドルのドラッグによる切り出し範囲調整（すべて外縁float座標のままフレーム内へ自動クランプして保存）。
   - `+ 膜` で元写真上をドラッグして新規メンブレン領域＋セレクションを追加（メンブレン段階のみ）、`+ 段` で選択中メンブレン内に任意の矩形セレクションを追加（バンド段階のみ）。
   - 同一膜内の別バンド行候補（破線多角形）のクリック切替、他メンブレンのクリック選択（メンブレン段階のみ）、ホイール拡大・ドラッグ移動。
4. **下部ペイン（飽和赤ドット `●` 付き露光ストリップ `ExposureStrip.svelte`）**:
   - バンド段階でのみ表示。
   - 選択段の全露光サムネイルを横並び表示し、採用露光へ自動スクロール。
   - 飽和画素を含む露光には赤ドット `●` を表示。シングルクリックで比較プレビュー（`.wblot` 非変更）、`✓` またはダブルクリックで採用。

### 4.2 構造化 `evidence` の自動記録原則
人間がUI上で確認・確定操作を行った際、自由記述テキストの入力を強要せず、構造化 `evidence`（`{"confirmed_by": "human_ui", "mode": "...", "timestamp": "..."}`）をシステムが自動生成して記録する。AI Agentが推測のみで `confirmed` に設定することは厳格に禁止され、利用者が明示的に承認した場合にのみ承認APIを呼び出す。

---

## 5. HTTP API 契約

FastAPIサーバー（127.0.0.1）が提供するエンドポイント一覧。変更系リクエストはすべて `expected_revision` を伴い、成功時は保存後の `Project` JSON を返す。

| メソッド | パス | リクエスト主要項目 | レスポンス・説明 |
| --- | --- | --- | --- |
| `GET` | `/api/project` | なし | `.wblot` の完全なJSONデータ |
| `GET` | `/api/revision` | なし | `{"project_revision": n}`（画面のポーリング用） |
| `GET` | `/api/status` | なし | `wbm status --json` と同等。段一覧、未確定項目、確定出力可否判定（`readiness`） |
| `GET` | `/api/layout` | なし | Python `layout.py` による完成図配置情報（`included=True` の段のみ） |
| `POST` | `/api/apply_proposal` | `{proposal, expected_revision}` | 提案の反映（`status: candidate` として記録） |
| `POST` | `/api/select_band_row` | `{panel_id, candidate_id, expected_revision}` | 同一膜内の別バンド行候補への切り替え |
| `POST` | `/api/set_selected_exposure` | `{panel_id, capture_id, expected_revision}` | 採用露光の変更 |
| `POST` | `/api/save_panel_edit` | `{panel_id, expected_revision, region?, display_geometry?, annotations?, selected_exposure_id?}` | パネル幾何・切り出し範囲・角度のアトミック保存 |
| `POST` | `/api/add_manual_membrane` | `{rect?, polygon?, reference_frame_id?, rotation_deg?, user_label?, label?, expected_revision}` | 手動での新規メンブレン領域・セレクション・Colorimetricマーカー登録追加 |
| `POST` | `/api/add_panel_selection` | `{panel_id, crop_rect, rotation_deg?, label?, expected_revision}` | 既存メンブレンへの手動矩形セレクション（別段）追加 |
| `POST` | `/api/rename_panel` | `{panel_id, label, expected_revision}` | 段ラベル（抗体名等）の変更 |
| `POST` | `/api/set_panel_included` | `{panel_id, included: bool, expected_revision}` | 段の採用／除外切り替え |
| `POST` | `/api/duplicate_panel` | `{panel_id, candidate_id?, expected_revision}` | 段の複製（同一膜の別バンド用、独立した変換を保持） |
| `POST` | `/api/reorder_panels` | `{panel_ids: list[str], expected_revision}` | 段の表示順序（`display_order`）の一括更新 |
| `POST` | `/api/set_lanes` | `{lane_count: int, panel_id?, sample_names?, expected_revision}` | レーン数および試料名リストの設定 |
| `POST` | `/api/set_lane_boundaries` | `{panel_id, boundaries: list[float], expected_revision}` | 段ごとのレーン境界位置（0..1正規化、両端0/1、最小間隔 `min(0.02, 0.5/レーン数)`）の保存 |
| `POST` | `/api/confirm_transform` | `{transform_id, evidence, expected_revision}` | 個別座標変換の目視確定 |
| `POST` | `/api/confirm_alignment` | `{image_set_id, evidence, expected_revision}` | 系列内位置合わせの目視確定 |
| `POST` | `/api/confirm_panel` | `{panel_id, evidence, expected_revision}` | 段単体の `review_status` 確定 |
| `POST` | `/api/approve_panel` | `{panel_id, evidence, expected_revision}` | 段・領域割当・関連幾何変換・Colorimetricマーカーの一括目視確定 |
| `POST` | `/api/approve_all` | `{evidence, expected_revision}` | 表示中（`included=True`）の全段と関連変換・Colorimetricマーカーの一括確定 |
| `POST` | `/api/export` | `{kind: "pptx"\|"png", final: bool}` | PPTXまたはPNGファイル出力。PPTXは `<root>_<draft\|final>_<YYYYMMDD-HHMMSS-ffffff+ZZZZ>.pptx` のタイムスタンプ付き一意名で既存ファイルを上書きしない（PNGは従来どおり固定名） |
| `GET` | `/api/image/frame/{frame_id}.png` | クエリ `max_width?` | 元写真全体の縮小プレビュー配信 |
| `GET` | `/api/image/panel/{panel_id}/{capture_id}.png` | なし | 切り出し済みパネル画像配信 |
| `GET` | `/api/image/marker/{panel_id}.png` | なし | ブロットと同一高さ範囲 `[crop.y0, crop.y1]` のColorimetricマーカー切り出し画像配信 |

### PPTX専用 Colorimetric マーカーストリップ

プレビュー・PNGは従来どおりブロット右側に登録マーカー1枚のみを同一高さで配置する。PPTX（`compute_layout(project, pptx_markers=True)` → `export_pptx` のみが使用）は、登録済みColorimetricフレームから各行最大3枚の独立編集可能な画像を順に並べる:

1. **main**: 登録マーカーROIのx範囲を保ちつつ、バンド切り出し高さの最大8%を上下に bleed した帯。
2. **left / right**: バンド切り出しの左右すぐ隣の帯（公称幅は切り出し幅の10%）。

幾何は `rendering.marker_export_rects(project, panel)` が rectified 座標で算出し、逆変換後の多角形が実Colorimetricフレーム内に収まるまで bleed（0.08→0.04→0）および側帯幅（0.10→0.05→0.02）を段階的に縮小する。メイン帯を含め、収まらない帯は省略し、余白を生成・補完しない。各画像はブロットと同一のx/yスケールで配置（拡大・引き伸ばしなし）し、物理bleedは `min(0.06in, 0.08×ブロット高)` 以下にキャップして行間0.14in内の衝突を防ぐ。マーカー未登録の段は従来の `marker_note` のまま、未登録マーカーを捏造しない。`render_marker(project, root, panel, part=...)` が `kind='marker_export'` のキャッシュ付き単一ワープで各部分を描画する。

なお、段ごとの `lane_layout.boundaries`（カスタムレーン幅）はHTML・PPTX・PNGのサンプル表にも反映され、セル幅が各段の境界に追従する。レイアウトが異なる段が連続する場合は、その段の直前に対応する幅のサンプル表を繰り返し挿入する。

---

## 6. CLI (`wbm`) 仕様とレシート契約

すべてのコマンドで `--project <パス>`（省略時はカレントディレクトリの `.wblot`）および `--json` が利用可能である。

### 6.1 コマンド一覧

| コマンド | 引数・オプション | 概要 |
| --- | --- | --- |
| `wbm init` | `<ZIPまたは画像フォルダ>` | `.wblot` を作成して初期化 |
| `wbm open` | `[<パス>] [--no-browser]` | バックグラウンドサーバーを起動し、既定ブラウザでワークベンチを開く |
| `wbm stop` | なし | 起動中のバックグラウンドサーバーを停止 |
| `wbm status` | なし | 段一覧、採用露光、未確定項目、確定出力可否を表示 |
| `wbm propose` | `[--apply] [--summary]` | 自動提案を計算。`--apply` で反映、`--summary` で要約表示 |
| `wbm rows` | `[<段>]` | 各段で利用可能なバンド行候補一覧を表示 |
| `wbm select-row` | `<段> <候補ID>` | 段のバンド行候補を切り替え |
| `wbm set-exposure` | `<段> <撮影ID>` | 採用露光を変更 |
| `wbm adjust` | `<段> [--rotation D] [--crop-left X0] [--crop-right X1] [--crop-top Y0] [--crop-bottom Y1] [--width PX]` | 角度・切り出し4辺・出力幅を微調整 |
| `wbm add-membrane` | `<x0> <y0> <x1> <y1> [--rotation D] [--membrane-label M] [--label L] [--frame F]` | 手動で新規メンブレン領域とセレクションを追加 |
| `wbm rename` | `<段> <ラベル>` | 段の表示ラベルを変更 |
| `wbm reorder` | `<段1> <段2> ...`（カンマ区切りも可） | 段の並び順（表示順）を変更 |
| `wbm include` | `<段>` | 段を図版に含める（`included=True`） |
| `wbm exclude` | `<段>` | 段を図版から除外（`included=False`） |
| `wbm duplicate` | `<段> [--candidate <候補ID>]` | 段を複製（同一膜の別バンド用） |
| `wbm lanes` | `<レーン数> [--panel <段>] [--samples 試料1,試料2,...]` | レーン数と試料名を設定 |
| `wbm approve` | `[<段>] [--all] --evidence "..."` | 段（または全段）・関連変換・Colorimetricマーカーを目視確定 |
| `wbm confirm-alignment` | `<系列IDまたは変換ID> --evidence "..."` | 位置合わせ候補を目視確定 |
| `wbm confirm` | `<段> --evidence "..."` | 段の `review_status` を確定 |
| `wbm export` | `pptx\|png [--final]` | PPTXまたはPNGファイルを出力 |
| `wbm validate` | なし | 原画像のSHA-256ハッシュ照合と未確定項目を検証 |
| `wbm install-skill` | `[--claude] [--codex]` | Agent用Skillファイルを所定ディレクトリに配置 |

※ `<段>` はパネルラベル（`A`, `B` 等）または `panel_id` で指定可能。

### 6.2 変更系コマンドの `--json` レシート契約
AI Agentが変更のたびに巨大な `.wblot` 全体を再読み込みしてトークンを浪費するのを防ぐため、変更系コマンドは必要最小限の構造化レシートを返す。

- **単一パネル変更** (`select-row`, `set-exposure`, `adjust`, `add-membrane`, `rename`, `include`, `exclude`, `approve <段>`, `confirm`):
  ```json
  {
    "ok": true,
    "project_revision": 5,
    "panel": {
      "panel_id": "PANEL_REGION_M1_A",
      "label": "TargetA",
      "included": true,
      "display_order": 1,
      "lane_count": 6,
      "membrane_label": "M1",
      "region_id": "REGION_M1",
      "review_status": "confirmed",
      "region_assignment_status": "confirmed",
      "selected_band_row_candidate_id": "M1_R2",
      "selected_exposure_id": "CAP_IMG049",
      "exposure_s": 120.0,
      "exposure_reason": "longest_unsaturated_in_crop",
      "geometry_status": "set"
    }
  }
  ```
- **複数パネル変更** (`reorder`, `lanes`, `approve --all`):
  ```json
  {
    "ok": true,
    "project_revision": 6,
    "panels": [ /* 各パネルの要約配列 */ ]
  }
  ```
  （※ `approve --all` は `"readiness"` も含む）
- **パネル複製** (`duplicate`):
  ```json
  {
    "ok": true,
    "project_revision": 7,
    "new_panel_id": "PANEL_REGION_M1_A_DUP1",
    "panel": { /* 複製された新規パネル要約 */ }
  }
  ```
- **提案反映** (`propose --apply`):
  ```json
  {
    "ok": true,
    "project_revision": 2,
    "panels": [ /* 初期提案されたパネル配列 */ ],
    "excluded_image_sets": [ /* 除外された系列と理由 */ ],
    "warnings_count": 0
  }
  ```

---

## 7. AI Agent 連携ワークフロー

AI Agentは以下の標準手順に従って人間と協調しながら作業を進める。

1. **初期化と自動提案**:
   `wbm init "<ZIPまたはフォルダ>" --json` を実行し、取得したパスに対して `wbm propose --apply --project "<project>" --json` で初期提案を反映。
2. **状態確認とユーザーへの報告**:
   `wbm status --project "<project>" --json` で段構成・採用露光・未確定項目を取得し、要点を簡潔にユーザーに報告。
3. **ブラウザ確認の依頼**:
   `wbm open "<project>" --json` でバックグラウンドサーバーを起動し、ブラウザで直接確認するようユーザーに依頼。
4. **対話的な修正の反映**:
   ユーザーからのフィードバック（段の変更、手動メンブレン／セレクション追加、角度微調整、露光切り替え、ラベル名指定、並べ替え、除外、レーン数設定等）を CLI コマンド（`select-row`, `add-membrane`, `adjust`, `rename`, `set-exposure`, `reorder`, `include`/`exclude`, `duplicate`, `lanes`）で迅速に反映。画面側は2秒ごとに更新を自動検知して即座に再描画する。
5. **目視承認の実施**:
   ユーザーが明示的に認めた場合に限り、`wbm approve <段> --evidence "..."` または `wbm approve --all --evidence "..."`、および `wbm confirm-alignment` を実行。推測による確定は行わない。
6. **ファイル出力と完了**:
   `wbm export pptx --project "<project>" --json`（確定版は `--final`）を実行して出力ファイルパスをユーザーに通知。作業完了後は `wbm stop --project "<project>" --json` でサーバーを停止。
