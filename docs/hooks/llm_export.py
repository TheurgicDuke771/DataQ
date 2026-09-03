"""MkDocs hook: publish every page as raw Markdown, plus llms.txt / llms-full.txt.

Each rendered page also gets `<page url>index.md` — its source Markdown, verbatim — so a
reader (or their AI assistant) can fetch the page without the HTML chrome. The header
action row (docs/overrides/main.html) links to it: View as Markdown · Copy as Markdown ·
Ask AI. `llms.txt` is the site index in the llms.txt convention; `llms-full.txt`
concatenates every page. Runs inside the normal build, so the versioned site carries them
per version.
"""

from __future__ import annotations

from pathlib import Path

_pages: list[tuple[str, str, str, str]] = []  # (url, title, markdown, section)


def on_page_markdown(markdown: str, page, config, files) -> str:
    if page.url.startswith("adr/"):
        section = "Decision records (ADRs)"
    elif page.ancestors:
        section = page.ancestors[-1].title
    else:
        section = "Home"
    _pages.append((page.url, page.title or page.file.name, markdown, section))
    return markdown


def on_post_build(config) -> None:
    site = Path(config["site_dir"])
    base = config["site_url"].rstrip("/") + "/"
    index: dict[str, list[str]] = {}
    full: list[str] = [f"# {config['site_name']} documentation\n"]
    for url, title, markdown, section in _pages:
        out = site / url / "index.md" if url else site / "index.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        first = next(
            (ln.strip() for ln in markdown.splitlines() if ln.strip() and not ln.startswith("#")),
            "",
        )
        index.setdefault(section, []).append(f"- [{title}]({base}{url}index.md): {first[:160]}")
        full.append(f"\n\n---\n\n<!-- {base}{url} -->\n\n{markdown}")
    lines = [
        f"# {config['site_name']}",
        "",
        f"> {config.get('site_description', '').strip()}",
        "",
        "Every page below is available as raw Markdown at the linked `.md` URL. The same pages are",
        "served to AI assistants by DataQ's MCP `get_doc` tool.",
        "",
    ]
    for section, items in index.items():
        lines += [f"## {section}", "", *items, ""]
    lines += ["## Optional", "", f"- [Everything in one file]({base}llms-full.txt)", ""]
    (site / "llms.txt").write_text("\n".join(lines), encoding="utf-8")
    (site / "llms-full.txt").write_text("".join(full), encoding="utf-8")
    _pages.clear()
