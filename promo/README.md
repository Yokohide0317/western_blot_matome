# プロモーション動画

架空のデモデータで実際の画面を録画し、Remotionで約30秒の動画にします。実データは使いません。

```bash
# 1. 架空のChemiDoc書き出しを作り、取り込む（パスにユーザー名が出ない共有フォルダを使う）
uv run python promo/make_demo_data.py /Users/Shared/blot/ChemiDoc_export.zip
uv run wbm init /Users/Shared/blot/ChemiDoc_export.zip
uv run wbm propose --apply --project /Users/Shared/blot/ChemiDoc_export
uv run wbm open /Users/Shared/blot/ChemiDoc_export --no-browser --json   # url を控える

# 2. 画面を録画（2倍解像度の連番JPEGを promo/public/rec/ に書き出す）
uv run python promo/record.py "<url>" /Users/Shared/blot/ChemiDoc_export

# 3. 完成図（PNG）を書き出して promo/public/shots/ に置く（figure.png、figure-crop.png、icon.png）

# 4. 動画にする
cd promo && npm install
npx remotion studio src/index.ts                       # 確認
npx remotion render src/index.ts Promo out/wbm-promo.mp4 --crf=18 \
  --browser-executable="$HOME/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell"
```

- 録画・画像・動画（`public/rec/`、`public/shots/`、`out/`、`data/`）はgit管理外です。
- Remotion付属のブラウザでは描画が止まることがあったため、Playwrightのブラウザを指定しています。
- 普段の作業用の設定を汚さないよう、`WBM_HOME` などを一時フォルダに向けて実行してください（`dev/AGENT_POLICY.md`）。
- Remotion には利用条件があります（https://remotion.dev/license）。
