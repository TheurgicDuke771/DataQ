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

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
OUT = ROOT / "docs/site/reference/check-types.md"
ALLOWLIST = ROOT / "backend/app/datasources/expectation_allowlist.py"
UC = ROOT / "backend/app/datasources/unity_catalog.py"
DUMP_DIR = FRONTEND / "node_modules/.cache/docs-catalog"
EXAMPLES_MODULE = ROOT / "scripts/docs/check_examples.py"

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
    "Aggregate",
    "Schema",
    "Anomaly",
    "Comparison",
    "Custom SQL",
    "Snowflake DMF",
    "Databricks DQX",
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
    "Aggregate": "Does a column statistic — mean, median, sum, standard deviation, min or "
    + "max — stay inside two-sided bands? The value is recorded every run, so it trends; an "
    + "empty table or all-NULL column reports error, never a made-up 0.",
    "Schema": "Did the table's columns change against a captured baseline?",
    "Anomaly": "Is today's value unusual against a rolling baseline of this check's own history? "
    + "Skips until enough history exists.",
    "Comparison": "Reconcile the suite's target against a second dataset, "
    + "possibly on another connection.",
    "Custom SQL": "Any predicate you can write in SQL, validated before it runs.",
    "Snowflake DMF": "Snowflake's native Data Metric Functions, evaluated inside Snowflake.",
    "Databricks DQX": (
        "Databricks Labs DQX row rules, evaluated by a serverless job in your own workspace."
    ),
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
    if entry["kind"] == "aggregate":
        return "Two-sided warn / fail / critical bounds, set as parameters"
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
    dialect_gap = [t for t in types if t in entry.get("unsupportedOn", [])]
    if dialect_gap:
        types = [t for t in types if t not in dialect_gap]
        note += (
            " — not "
            + ", ".join(labels[t] for t in dialect_gap)
            + " (no translation for that SQL dialect; refused at author time)"
        )
    if entry["kind"] == "aggregate":
        note += (
            " — median not on "
            + ", ".join(labels[t] for t in ("mysql", "trino", "athena"))
            + " (no exact median; refused at author time)"
        )
    if entry["type"] in pushdown and "unity_catalog" in types:
        note += " · SQL pushdown on Unity Catalog"
    names = (
        "All datasources" if set(types) == set(ds["all"]) else ", ".join(labels[t] for t in types)
    )
    return names + note


def load_examples() -> Any:
    spec = importlib.util.spec_from_file_location("check_examples", EXAMPLES_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _code(value: Any) -> str:
    return f"`{json.dumps(value)}`"


def sample_table(ex: Any) -> list[str]:
    lines = [
        '??? info "The sample table every example below runs on"',
        "",
        "    Ten orders, with one deliberate defect in most columns.",
        "",
        "    | " + " | ".join(ex.SAMPLE_COLUMNS) + " |",
        "    |" + "---|" * len(ex.SAMPLE_COLUMNS),
    ]
    for row in ex.SAMPLE_ROWS:
        cells = ["NULL" if v is None else str(v).replace("|", "\\|") for v in row]
        lines.append("    | " + " | ".join(cells) + " |")
    return [*lines, ""]


def outcome(result: dict[str, Any]) -> str:
    parts = [f"**{result['status'].upper()}**"]
    count = result["unexpected_count"]
    if count is not None and result["metric_value"] is not None:
        parts.append(
            f"{result['metric_value']:g}% unexpected ({count} row{'' if count == 1 else 's'})"
        )
    elif result["observed_value"] is not None:
        parts.append(f"observed {_code(result['observed_value'])}")
    if result["unexpected_values"]:
        parts.append("unexpected values " + _code(result["unexpected_values"]))
    return " · ".join(parts)


def example_block(entry: dict, ex: Any, results: dict[str, Any]) -> list[str]:
    example = ex.EXAMPLES.get(entry["type"])
    if example is None:
        raise SystemExit(f"no example for {entry['type']!r}: add one to {EXAMPLES_MODULE.name}")
    body: dict[str, Any] = {"name": entry["label"], "expectation_type": entry["type"]}
    if example.kind != "expectation":
        body["kind"] = example.kind
    if example.engine != "gx":
        body["engine"] = example.engine
    body["config"] = example.config
    if example.kind == "comparison":
        body["source_connection_id"] = "<connection id>"
    body.update(example.thresholds)
    lines = [
        f'??? example "{entry["label"]}"',
        "",
        "    ```json",
        *("    " + line for line in json.dumps(body, indent=2).splitlines()),
        "    ```",
        "",
    ]
    result = results.get(entry["type"])
    if result is not None:
        lines += [f"    **On the sample:** {outcome(result)}", ""]
    return [*lines, f"    {example.note}", ""]


def render(
    catalog: list[dict],
    caps: dict[str, str],
    only: set[str],
    pushdown: set[str],
    ds: dict,
    ex: Any,
    results: dict[str, Any],
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
        "Each section ends with **examples**: the body you would send to create the check "
        + "(`POST /api/v1/suites/{suite_id}/checks`; the same fields work over MCP and in a "
        + "suite import) and, for every type that runs on a plain table, the result DataQ "
        + "reports on the sample below. Those results are produced by DataQ's own check "
        + "engine and re-checked in CI.",
        "",
        *sample_table(ex),
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
        lines += ["", "### Examples", ""]
        for e in entries:
            lines += example_block(e, ex, results)
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
        "**GX's scalar aggregates** (`expect_column_mean_to_be_between` and its siblings) "
        + "report one",
        "number and no unexpected-%, so severity bands have nothing to band — the Aggregate "
        + "monitor",
        "measures that shape instead, with two-sided bands and a trend. **Whole-table column-set",
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
    ex = load_examples()
    results = json.loads(ex.RESULTS.read_text())
    text = render(catalog, caps, only, parse_pushdown(), ds, ex, results)
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
