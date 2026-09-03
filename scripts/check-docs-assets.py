#!/usr/bin/env python3
"""Published docs assets stay small and every image has alt text.

The docs site is served from GitHub Pages and read on laptops on hotel Wi-Fi: a
screenshot over ~350 KB or a clip over ~2 MB is a capture-lane mistake (wrong scale,
un-transcoded webm), not a decision. Alt text is what a screen reader — and an AI
assistant reading the Markdown — gets instead of the picture.
"""

from __future__ import annotations

import re
from pathlib import Path

SITE = Path("docs/site")
LIMITS = {".png": 350 * 1024, ".jpg": 150 * 1024, ".mp4": 2 * 1024 * 1024}
IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)")
# Raw HTML is NOT path-rewritten by MkDocs: a <video>/<source>/poster path must be relative to
# the page's directory URL (get-started/first-suite.md → get-started/first-suite/), which is
# one level deeper than the .md file. A wrong path ships as a blank box, so resolve it here.
RAW_SRC = re.compile(r'<(?:source|video|img)[^>]*?\s(?:src|poster)="(?P<src>[^"]+)"')


def main() -> int:
    problems: list[str] = []
    for path in sorted((SITE / "assets").rglob("*")):
        limit = LIMITS.get(path.suffix.lower())
        if limit and path.stat().st_size > limit:
            problems.append(f"{path}: {path.stat().st_size // 1024} KB > {limit // 1024} KB")
    for md in sorted(SITE.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for m in IMAGE.finditer(text):
            if not m.group("alt").strip():
                problems.append(f"{md}: image {m.group('src')} has no alt text")
        page_dir = md.parent if md.name == "index.md" else md.with_suffix("")
        for m in RAW_SRC.finditer(text):
            src = m.group("src")
            if src.startswith(("http://", "https://", "/")):
                continue
            if not (page_dir / src).resolve().exists():
                problems.append(
                    f"{md}: raw src {src} does not resolve from the page URL {page_dir}/ "
                    "(raw HTML paths are not rewritten by MkDocs)"
                )
    if problems:
        print("Docs asset check failed:\n  " + "\n  ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
