#!/usr/bin/env python3
"""Generate docs/site/reference/check-types.md from the code that defines the check types.

Sources (never hand-typed, so the page cannot drift from the product):
  * frontend/src/components/checks/expectationCatalog.ts — what the editor offers
    (label, description, category, dimension, parameters), dumped to JSON via
    frontend/scripts/dump-catalog.mts (Vite SSR build; extensionless TS imports need a bundler)
  * backend/app/datasources/expectation_allowlist.py — what the backend will author
    (allowlist-only types, dataframe-only, unbandable), parsed textually so this runs
    without the backend environment
  * backend/app/datasources/unity_catalog.py — SQL_PUSHDOWN_EXPECTATION_TYPES

Usage: scripts/docs/gen-check-catalog.py [--check]   (--check: exit 1 if the page is stale)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
OUT = ROOT / "docs/site/reference/check-types.md"
ALLOWLIST = ROOT / "backend/app/datasources/expectation_allowlist.py"
UC = ROOT / "backend/app/datasources/unity_catalog.py"
DUMP_DIR = FRONTEND / "node_modules/.cache/docs-catalog"

DIMENSION_LABEL = {
    "accuracy": "Accuracy",
    "completeness": "Completeness",
    "consistency": "Consistency",
    "integrity": "Integrity",
    "timeliness": "Timeliness",
    "uniqueness": "Uniqueness",
    "validity": "Validity",
    None: "— (set it yourself)",
}
CATEGORY_ORDER = [
    "Column values",
    "Table shape",
    "Freshness",
    "Volume",
    "Schema",
    "Anomaly",
    "Comparison",
    "Custom SQL",
    "Snowflake DMF",
]
CATEGORY_INTRO = {
    "Column values": "Great Expectations built-ins that look at the values in one or more columns. "
    "Each returns an unexpected-% that the warn / fail / critical severity bands read.",
    "Table shape": "Whole-table expectations.",
    "Freshness": "How stale is the target? Measured from a timestamp column (or "
    "file arrival time on "
    "flat files), reported in hours, banded by age. Requires a fail or critical threshold.",
    "Volume": "Did the load deliver the expected row count? Banded by count. Requires a fail or "
    "critical threshold.",
    "Schema": "Did the table's columns change against a captured baseline?",
    "Anomaly": "Is today's value unusual against a rolling baseline of this check's own history? "
    "Skips until enough history exists.",
    "Comparison": "Reconcile the suite's target against a second dataset, "
    "possibly on another connection.",
    "Custom SQL": "Any predicate you can write in SQL, validated before it runs.",
    "Snowflake DMF": "Snowflake's native Data Metric Functions, evaluated inside Snowflake.",
}


def dump_catalog() -> list[dict]:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "pnpm",
            "exec",
            "vite",
            "build",
            "--ssr",
            "scripts/dump-catalog.mts",
            "--outDir",
            str(DUMP_DIR),
            "--logLevel",
            "error",
        ],
        cwd=FRONTEND,
        check=True,
    )
    raw = subprocess.run(  # noqa: S603
        ["node", str(DUMP_DIR / "dump-catalog.js")],  # noqa: S607
        cwd=FRONTEND,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(raw)


def parse_allowlist() -> tuple[dict[str, str], set[str]]:
    text = ALLOWLIST.read_text()
    body = text[text.index("ALLOWED_EXPECTATIONS") : text.index("ALLOWED_EXPECTATION_TYPES")]
    caps = dict(re.findall(r'"(expect_[a-z_]+)":\s*(_[A-Z_]+)', body))
    only = set(
        re.findall(
            r'"(expect_[a-z_]+)"',
            text[
                text.index("ALLOWLIST_ONLY_TYPES") : text.index("DATAFRAME_ONLY_EXPECTATION_TYPES")
            ],
        )
    )
    return caps, only


def parse_pushdown() -> set[str]:
    text = UC.read_text()
    start = text.index("SQL_PUSHDOWN_EXPECTATION_TYPES")
    end = text.index(")", text.index("frozenset(", start))
    return set(re.findall(r'"(expect_[a-z_]+)"', text[start:end]))


def params(entry: dict) -> str:
    parts = []
    for f in entry["fields"]:
        name = f"`{f['name']}`"
        parts.append(f"{name} *(optional)*" if f["optional"] else name)
    return ", ".join(parts) or "—"


def thresholds(entry: dict, cap: str | None) -> str:
    if entry["noThresholds"] or cap == "_UNBANDED":
        return "None — pass/fail only"
    if entry["requireFailOrCritical"]:
        return "warn / fail / critical (fail or critical required)"
    return "warn / fail / critical"


def runs_on(entry: dict, cap: str | None, pushdown: set[str]) -> str:
    t = entry["type"]
    if entry["engine"] == "dmf":
        return "Snowflake only (native DMF)"
    if entry["kind"] == "anomaly":
        return "Snowflake, Unity Catalog"
    if entry["kind"] in {"freshness", "volume", "schema_drift"}:
        return "All datasources"
    if entry["kind"] == "comparison":
        return "All datasources"
    if t == "custom_sql" or entry["category"] == "Custom SQL":
        return "Snowflake, Unity Catalog"
    if cap == "_DATAFRAME_ONLY" or entry["dataframeOnly"]:
        return (
            "Flat files (ADLS Gen2 / S3), Iceberg, Unity Catalog — not Snowflake "
            "(no SQL implementation; refused at author time)"
        )
    note = " · SQL pushdown on Unity Catalog" if t in pushdown else ""
    return "All datasources" + note


def render(catalog: list[dict], caps: dict[str, str], only: set[str], pushdown: set[str]) -> str:
    by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for e in catalog:
        by_cat.setdefault(e["category"], []).append(e)
    lines = [
        "# Check types",
        "",
        "Every kind of check DataQ can author, generated from the check editor's catalog and the",
        "backend's vetted allowlist — so this page cannot drift from what the product actually",
        "offers. Each GX type on this page has been executed on both a "
        "dataframe and a SQL batch in CI.",
        "",
        "| | Count |",
        "|---|---|",
        f"| Check types in the editor | {len(catalog)} |",
        f"| GX expectation types vetted by the backend | {len(caps)} |",
        "",
        "How to read a row: **Parameters** are the editor's fields (`mostly` is GX's optional row",
        "tolerance, a fraction). **Thresholds** are the severity bands read from the result.",
        "**Dimension** is the default data-quality dimension the check is "
        "classified under; you can",
        "change it on any check.",
        "",
    ]
    for cat in CATEGORY_ORDER:
        entries = by_cat.get(cat) or []
        if not entries:
            continue
        lines += [
            f"## {cat}",
            "",
            CATEGORY_INTRO.get(cat, ""),
            "",
            "| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |",
            "|---|---|---|---|---|---|---|",
        ]
        for e in entries:
            cap = caps.get(e["type"])
            lines.append(
                f"| **{e['label']}** | `{e['type']}` | {e['description']} | "
                f"{DIMENSION_LABEL.get(e['dimension'])} | {params(e)} | {thresholds(e, cap)} | "
                f"{runs_on(e, cap, pushdown)} |"
            )
        lines.append("")
    if only:
        lines += [
            "## Authorable outside the editor",
            "",
            "Vetted by the backend but with no editor widget: usable over the REST API, MCP and",
            "suite import, which hand the backend raw JSON.",
            "",
        ]
        for t in sorted(only):
            lines.append(f"- `{t}`")
        lines.append("")
    lines += [
        "## Not offered, and why",
        "",
        "**Scalar aggregates** (`expect_column_mean_to_be_between` and its "
        "siblings) report one number",
        "and no unexpected-%, so severity bands have nothing to band — a Volume or Anomaly monitor",
        "measures that shape with trends and a learned baseline. **Whole-table column-set",
        "comparisons** are what the Schema-drift monitor does against a captured baseline. For",
        "anything else, write a custom-SQL check.",
        "",
        "---",
        "",
        "*Generated by `scripts/docs/gen-check-catalog.py` — edit the catalog or the allowlist, "
        "not this page.*",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    catalog = dump_catalog()
    caps, only = parse_allowlist()
    text = render(catalog, caps, only, parse_pushdown())
    if check:
        if OUT.exists() and OUT.read_text() == text:
            return 0
        print(
            f"{OUT.relative_to(ROOT)} is stale — run scripts/docs/gen-check-catalog.py",
            file=sys.stderr,
        )
        return 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(catalog)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
