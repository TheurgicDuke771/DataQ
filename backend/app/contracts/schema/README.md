# Vendored ODCS JSON Schemas

The [Open Data Contract Standard](https://bitol-io.github.io/open-data-contract-standard/)
JSON Schemas, copied verbatim from
[bitol-io/open-data-contract-standard](https://github.com/bitol-io/open-data-contract-standard/tree/main/schema)
(Bitol, an LF AI & Data Foundation project). Licensed **Apache-2.0** — the upstream `LICENSE`
sits beside them; CONTRIBUTING rule 40 / ADR 0031 allow it with this notice.

| File | Upstream commit / date |
|---|---|
| `odcs-json-schema-v3.0.0.json` … `v3.2.0.json` | `main` as of 2026-10-25 |

Pinned by hand like Great Expectations (ADR 0052 §5): a new ODCS minor is a deliberate bump
here plus a row in the translation table's tests, never an automatic fetch. The loader in
`../odcs.py` picks the file for a document's `apiVersion`; a newer v3 minor than the newest file
is validated against that newest file with a warning.
