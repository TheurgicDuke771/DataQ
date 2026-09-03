"""MkDocs hook: publish every page as raw Markdown, plus llms.txt / llms-full.txt.

Each rendered page also gets `<page url>index.md` — its source Markdown, verbatim — so a
reader (or their AI assistant) can fetch the page without the HTML chrome. The header
action row (docs/overrides/main.html) links to it: View as Markdown · Copy as Markdown ·
Ask AI. `llms.txt` is the site index in the llms.txt convention; `llms-full.txt`
concatenates every page. Runs inside the normal build, so each mike-published version
carries its own copies.
"""

from __future__ import annotations

from pathlib import Path

_pages: list[tuple[str, str, str, str, str]] = []  # (url, title, markdown, section, summary)
_ADR_SECTION = "Decision records (ADRs)"


def _summary(page, markdown: str) -> str:
    """One line for llms.txt: the page's `description` meta, else its first prose line."""
    meta = (page.meta or {}).get("description")
    if meta:
        return str(meta).strip()
    for ln in markdown.splitlines():
        t = ln.strip()
        if not t or t.startswith(("#", "-", "*", "|", ">", "!", "<", "```", ":")):
            continue
        return t[:160]
    return ""


def on_page_markdown(markdown: str, page, config, files) -> str:
    if page.url.startswith("adr/"):
        section = _ADR_SECTION
    elif page.ancestors:
        section = page.ancestors[-1].title
    else:
        section = "Home"
    _pages.append(
        (page.url, page.title or page.file.name, markdown, section, _summary(page, markdown))
    )
    return markdown


def on_post_build(config) -> None:
    site = Path(config["site_dir"])
    base = config["site_url"].rstrip("/") + "/"
    # Sections in nav order (top-level nav titles), then the ADRs.
    order = ["Home" if not isinstance(item, dict) else next(iter(item)) for item in config["nav"]]
    order.append(_ADR_SECTION)
    index: dict[str, list[str]] = {t: [] for t in order}
    full_by_section: dict[str, list[str]] = {t: [] for t in order}
    for url, title, markdown, section, summary in _pages:
        out = site / url / "index.md" if url else site / "index.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        line = f"- [{title}]({base}{url}index.md)" + (f": {summary}" if summary else "")
        index.setdefault(section, []).append(line)
        full_by_section.setdefault(section, []).append(
            f"\n\n---\n\n<!-- {base}{url} -->\n\n{markdown}"
        )
    lines = [
        f"# {config['site_name']}",
        "",
        f"> {config.get('site_description', '').strip()}",
        "",
        "Every page below is available as raw Markdown at the linked `.md` URL. A curated subset",
        "of them is also served to AI assistants by DataQ's MCP `get_doc` tool (its `page` enum",
        "lists which).",
        "",
    ]
    for section, items in index.items():
        if items:
            lines += [f"## {section}", "", *items, ""]
    lines += ["## Optional", "", f"- [Everything in one file]({base}llms-full.txt)", ""]
    (site / "llms.txt").write_text("\n".join(lines), encoding="utf-8")
    full = [f"# {config['site_name']} documentation\n"]
    for chunks in full_by_section.values():
        full.extend(chunks)
    (site / "llms-full.txt").write_text("".join(full), encoding="utf-8")
    _pages.clear()
