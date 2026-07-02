# Contributing

The full working agreements (39 rules), commit/PR conventions, and module boundaries live
in **[CONTRIBUTING.md](https://github.com/TheurgicDuke771/DataQ/blob/main/CONTRIBUTING.md)**.
The short version:

- **Trunk-based**: branch off `main`, PR back, **squash-merge**, **conventional commits**
  (`feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `ci:`).
- **One functionality per commit**; manually test before the next.
- **CI gates block merge**: Ruff, Black `--check`, mypy, Bandit, pytest (backend);
  ESLint, Prettier, Vitest (frontend); secret scanning; CodeQL; dependency audit.
- **Defects → a GitHub issue**, then a PR with `Fixes #N` (no silent fixes).
- **Backward-compatible migrations only** (two-step deploys).

## Editing these docs

This site is **MkDocs Material** built from the repo's `docs/` folder and published to
GitHub Pages by the `docs` workflow on every push to `main`. To change a page, edit the
Markdown under `docs/` and open a PR — the **Edit** pencil on any page links straight to
the source. Preview locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve         # live-reload preview at http://127.0.0.1:8000
```

Keep modules **short and plain-language**; link to the in-repo source of truth (ADRs,
`CONTRIBUTING.md`, `deploy/README.md`) rather than duplicating it.

## Decision records

Architecture decisions are recorded as **ADRs** in
[`docs/adr/`](https://github.com/TheurgicDuke771/DataQ/tree/main/docs/adr).

## Ownership

Docs owner / reviewer: **@TheurgicDuke771** (see
[CODEOWNERS](https://github.com/TheurgicDuke771/DataQ/blob/main/.github/CODEOWNERS)).
