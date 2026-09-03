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
    + "Each returns an unexpected-% that the warn / fail / critical severity bands read.",
    "Table shape": "Whole-table expectations.",
    "Freshness": "How stale is the target? Measured from a timestamp column (or "
    + "file arrival time on "
    + "flat files), reported in hours, banded by age. Requires a fail or critical threshold.",
    "Volume": "Did the load deliver the expected row count? Banded by count. Requires a fail or "
    + "critical threshold.",
    "Schema": "Did the table's columns change against a captured baseline?",
    "Anomaly": "Is today's value unusual against a rolling baseline of this check's own history? "
    + "Skips until enough history exists.",
    "Comparison": "Reconcile the suite's target against a second dataset, "
    + "possibly on another connection.",
    "Custom SQL": "Any predicate you can write in SQL, validated before it runs.",
    "Snowflake DMF": "Snowflake's native Data Metric Functions, evaluated inside Snowflake.",
}


def dump_catalog() -> dict:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    # Fixed argv, repo-owned inputs: not user input (S603/S607 are about neither).
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


def parse_allowlist() -> dict[str, str]:
    """type -> capability sentinel, from the ALLOWED_EXPECTATIONS dict literal itself."""
    text = ALLOWLIST.read_text()
    m = re.search(r"^ALLOWED_EXPECTATIONS:.*?= \{(.*?)^\}", text, re.M | re.S)
    if not m:
        raise SystemExit("ALLOWED_EXPECTATIONS literal not found")
    return dict(re.findall(r'"(expect_[a-z_]+)":\s*(_[A-Z_]+)', m.group(1)))


def parse_pushdown() -> set[str]:
    text = UC.read_text()
    m = re.search(r"^SQL_PUSHDOWN_EXPECTATION_TYPES:.*?frozenset\((.*?)\)", text, re.M | re.S)
    if not m:
        raise SystemExit("SQL_PUSHDOWN_EXPECTATION_TYPES literal not found")
    return set(re.findall(r'"(expect_[a-z_]+)"', m.group(1)))


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


def runs_on(entry: dict, cap: str | None, pushdown: set[str], ds: dict) -> str:
    """Mirror of the editor's expectationsByCategoryFor(): which connection types see this type."""
    labels = ds["labels"]
    if entry["engine"] == "dmf":
        types = ["snowflake"]
    elif entry["category"] in ("Custom SQL", "Anomaly"):
        types = list(ds["sqlQueryable"])
    elif entry["category"] in ds["monitorCategories"]:
        types = list(ds["monitorCapable"])
    else:
        types = list(ds["all"])
    note = ""
    if entry["dataframeOnly"] or cap == "_DATAFRAME_ONLY":
        excluded = [t for t in types if t in ds["sqlBatch"]]
        types = [t for t in types if t not in ds["sqlBatch"]]
        if excluded:
            note = (
                " — not "
                + ", ".join(labels[t] for t in excluded)
                + " (no SQL implementation; refused at author time)"
            )
    if entry["type"] in pushdown and "unity_catalog" in types:
        note += " · SQL pushdown on Unity Catalog"
    names = (
        "All datasources" if set(types) == set(ds["all"]) else ", ".join(labels[t] for t in types)
    )
    return names + note


def render(
    catalog: list[dict], caps: dict[str, str], only: set[str], pushdown: set[str], ds: dict
) -> str:
    by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for e in catalog:
        if e["category"] not in by_cat:
            raise SystemExit(
                f"unknown catalog category {e['category']!r}: add it to CATEGORY_ORDER"
            )
        by_cat[e["category"]].append(e)
    lines = [
        "# Check types",
        "",
        "Every kind of check DataQ can author, generated from the check editor's catalog and the",
        "backend's vetted allowlist — so this page cannot drift from what the product actually",
        "offers. Every GX type on this page is executed in CI on a dataframe batch, and on a "
        + "SQL batch too unless its row says it is dataframe-only.",
        "",
        "| | Count |",
        "|---|---|",
        f"| Check types in the editor | {len(catalog)} |",
        f"| GX expectation types vetted by the backend | {len(caps)} |",
        "",
        "How to read a row: **Parameters** are the editor's fields (`mostly` is GX's optional row",
        "tolerance, a fraction). **Thresholds** are the severity bands read from the result.",
        "**Dimension** is the default data-quality dimension the check is "
        + "classified under; you can",
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
                f"{runs_on(e, cap, pushdown, ds)} |"
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
        + "siblings) report one number",
        "and no unexpected-%, so severity bands have nothing to band — a Volume or Anomaly monitor",
        "measures that shape with trends and a learned baseline. **Whole-table column-set",
        "comparisons** are what the Schema-drift monitor does against a captured baseline. For",
        "anything else, write a custom-SQL check.",
        "",
        "---",
        "",
        "*Generated by `scripts/docs/gen-check-catalog.py` — edit the catalog or the allowlist, "
        + "not this page.*",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    dump = dump_catalog()
    catalog, ds = dump["catalog"], dump["datasources"]
    caps = parse_allowlist()
    only = set(caps) - {e["type"] for e in catalog}
    text = render(catalog, caps, only, parse_pushdown(), ds)
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
