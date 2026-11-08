"""Refuse to publish research data: run before every commit/push (CI runs it too).

Checks tracked and staged files for image/data formats and large files, and,
with ``dev/private_terms.txt`` (one term per line, never committed), for those
terms in file contents and in the messages of commits about to be pushed.
A line ``term @ path/one, path/two`` allows that term in those files only; it is
still refused everywhere else and in commit messages.

    uv run python dev/check_public.py
    uv run python dev/check_public.py --since origin/main
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TERMS = ROOT / "dev" / "private_terms.txt"
BLOCKED_SUFFIXES = {".tif", ".tiff", ".scn", ".zip", ".wblot", ".pdf", ".jpg", ".jpeg", ".png", ".gif",
                    ".bmp", ".webp", ".heic", ".pptx", ".xlsx", ".csv", ".raw"}
# The app icon is the only image allowed in the repository.
ALLOWED = {"docs/icon.png", "src/wb/web/app/public/icon.png", "src/wb/web/app/dist/icon.png", "src/wb/web/hub/icon.png"}
MAX_BYTES = 2 * 1024 * 1024
PRIVATE_DIRS = {"private", "instructions"}  # kept on disk for local tests, never published
TEXT_SKIP = {"uv.lock"}


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def files() -> list[str]:
    tracked = set(_git("ls-files", "-z").split("\0"))
    staged = set(_git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0"))
    return sorted(p for p in tracked | staged if p and (ROOT / p).is_file())


def private_terms() -> list[tuple[str, set[str]]]:
    """``(term, files where it is allowed)`` from ``dev/private_terms.txt``."""
    if not TERMS.is_file():
        return []
    terms = []
    for line in TERMS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term, _, allowed = line.partition(" @ ")
        terms.append((term.strip(), {p.strip() for p in allowed.split(",") if p.strip()}))
    return terms


def _pattern(term: str) -> re.Pattern:
    # A term is matched as a word start, so "ABC" finds "ABC1" but not "xabc".
    return re.compile(r"(?<![0-9A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", re.I)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows runners default to cp1252.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="also check messages of commits after this ref (e.g. origin/main)")
    args = parser.parse_args(argv)
    problems = []
    terms = [(t, _pattern(t), allowed) for t, allowed in private_terms()]
    for name in files():
        path = ROOT / name
        if name.split("/")[0] in PRIVATE_DIRS:
            problems.append(f"非公開フォルダのファイル: {name}")
        if Path(name).suffix.lower() in BLOCKED_SUFFIXES and name not in ALLOWED:
            problems.append(f"画像・データ形式のファイル: {name}")
        if path.stat().st_size > MAX_BYTES and name not in TEXT_SKIP:
            problems.append(f"大きすぎるファイル（{path.stat().st_size // 1024} KB）: {name}")
        if terms and name not in TEXT_SKIP and name != "dev/private_terms.txt":
            text = path.read_text(encoding="utf-8", errors="ignore")
            problems += [f"非公開の語「{t}」: {name}" for t, pattern, allowed in terms
                         if name not in allowed and pattern.search(text)]
    if args.since:
        messages = _git("log", "--format=%H%n%B", f"{args.since}..HEAD")
        problems += [f"非公開の語「{t}」: コミットメッセージ" for t, pattern, _ in terms if pattern.search(messages)]
    if "dev/private_terms.txt" in files():
        problems.append("dev/private_terms.txt 自体が追跡されています")
    if problems:
        print("公開できない内容があります（dev/AGENT_POLICY.md）:", *problems, sep="\n  ")
        return 1
    note = "" if terms else "（dev/private_terms.txt が無いので語の検査はしていません）"
    print(f"OK: {len(files())} ファイルを確認しました{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
