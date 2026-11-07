# AGENTS.md

作業の前に必ず [dev/AGENT_POLICY.md](dev/AGENT_POLICY.md) を読んで従ってください。

最重要: 利用者の画像、画像やデータから読み取った分子名・レーン構成・ファイル名などの研究内容を、リポジトリ・コミット・PR・Issueに載せない（Actin・Hsp90・GAPDHなど一般的なローディングコントロール名は可）。コミットやpushの前に `uv run python dev/check_public.py --since origin/main` を実行する。
