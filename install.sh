#!/bin/sh
# Install Western Blot Matome (wbm) on macOS or Linux.
#
# Needs only curl: no Xcode Command Line Tools, git, Python or Node.js.
# uv (https://docs.astral.sh/uv/) is installed if missing and brings its own Python.
#
#   curl -LsSf https://github.com/Yokohide0317/western_blot_matome/releases/latest/download/install.sh | sh
#   curl -LsSf .../install.sh | sh -s -- v0.2.0      # a specific version
set -eu

REPO="Yokohide0317/western_blot_matome"
TAG="${1:-${WBM_VERSION:-latest}}"

say() { printf '%s\n' "$*"; }

if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV="$HOME/.local/bin/uv"
else
  say "uv をインストールしています…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [ -x "$candidate" ] && UV="$candidate" && break
  done
  [ -n "${UV:-}" ] || { say "uv のインストールに失敗しました"; exit 1; }
fi

if [ "$TAG" = "latest" ]; then
  # .../releases/latest redirects to .../releases/tag/vX.Y.Z (no API rate limit).
  TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/##')"
fi
case "$TAG" in
  v[0-9]*) ;;
  *) say "リリースが見つかりませんでした: $TAG"; exit 1 ;;
esac
VERSION="${TAG#v}"
WHEEL="https://github.com/$REPO/releases/download/$TAG/wbm-$VERSION-py3-none-any.whl"

say "wbm $VERSION をインストールしています…"
"$UV" tool install --force --python 3.12 "$WHEEL"
WBM="$("$UV" tool dir --bin)/wbm"
"$WBM" install-skill
"$WBM" --version
say ""
say "インストールしました: $WBM"
say "Claude Code / Codex に Skill「wblot」を入れました。ChemiDocのZIPやフォルダを渡して「まとめて」と頼んでください。"
