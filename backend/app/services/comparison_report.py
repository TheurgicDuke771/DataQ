"""Derive a comparison result's downloadable report (ADR 0015 §4, #795).

Reports are **derived on demand from the persisted, already-redacted buckets —
never stored**: a stored full-mismatch file would bypass the `sample_failures`
redaction path, escape the PII-minimisation retention sweep, and assume an
object store a BYOL deploy may not have. The caller passes the REDACTED sample
(the same `redact_sample_failures` output the read API serves) plus the
observed bucket counts; this module only formats.

CSV: one flat table — a `bucket` discriminator column + the union of sample
columns (spreadsheet-friendly, diffable). XLSX: a `summary` sheet (counts +
mismatch-%) and one sheet per non-empty bucket.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from backend.app.core.errors import DataQError

REPORT_FORMATS = ("csv", "xlsx")

# Bucket order is stable so two downloads of the same result are identical.
_BUCKETS = ("mismatched", "additional_in_source", "additional_in_target")

# Presence-filtered: the row grain carries the first block, the column grain
# (#799) the `*_values` block; identity keys appear in both.
_SUMMARY_KEYS = (
    "source_rows",
    "target_rows",
    "matched",
    "mismatched",
    "additional_in_source",
    "additional_in_target",
    "matched_values",
    "mismatched_values",
    "additional_in_source_values",
    "additional_in_target_values",
    "mismatch_percent",
)


class ComparisonReportInvalidError(DataQError):
    status_code = 422
    code = "comparison_report_invalid"


# Leading characters Excel/Sheets interpret as a formula — a cell carrying
# warehouse data must never execute on the analyst's machine (CSV/XLSX formula
# injection). The standard mitigation: prefix a single quote, which spreadsheet
# apps render as literal text.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(value: Any) -> Any:
    """Escape formula-leading strings; pass every other scalar through."""
    if isinstance(value, str) and value.startswith(_FORMULA_LEADERS):
        return f"'{value}"
    return value


def _bucket_rows(sample: dict[str, Any] | None) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for bucket in _BUCKETS:
        for row in (sample or {}).get(bucket) or []:
            if isinstance(row, dict):
                rows.append((bucket, row))
    return rows


def _columns(rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Union of sample columns in first-seen order (keys first by construction)."""
    seen: dict[str, None] = {}
    for _, row in rows:
        for col in row:
            seen.setdefault(str(col), None)
    return list(seen)


def build_csv(sample: dict[str, Any] | None, observed: dict[str, Any] | None) -> bytes:
    """The flat CSV: summary comment rows, then `bucket` + sample columns."""
    rows = _bucket_rows(sample)
    columns = _columns(rows)
    buf = io.StringIO()
    writer = csv.writer(buf)
    observed = observed or {}
    writer.writerow(["# comparison summary"])
    for key in _SUMMARY_KEYS:
        if key in observed:
            writer.writerow([f"# {key}", observed[key]])
    writer.writerow(["bucket", *columns])
    for bucket, row in rows:
        writer.writerow([bucket, *[_neutralize(row.get(col, "")) for col in columns]])
    return buf.getvalue().encode("utf-8")


def build_xlsx(sample: dict[str, Any] | None, observed: dict[str, Any] | None) -> bytes:
    """A workbook: `summary` sheet + one sheet per non-empty bucket."""
    # Lazy: openpyxl is only needed on this download path.
    from openpyxl import Workbook

    wb = Workbook()
    summary = wb.active
    assert summary is not None
    summary.title = "summary"
    summary.append(["metric", "value"])
    observed = observed or {}
    for key in _SUMMARY_KEYS:
        if key in observed:
            summary.append([key, observed[key]])

    for bucket in _BUCKETS:
        bucket_rows = [r for b, r in _bucket_rows(sample) if b == bucket]
        if not bucket_rows:
            continue
        sheet = wb.create_sheet(title=bucket[:31])  # 31-char Excel sheet-name cap
        columns = _columns([(bucket, r) for r in bucket_rows])
        sheet.append(columns)
        for row in bucket_rows:
            sheet.append([_cell(row.get(col)) for col in columns])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _cell(value: Any) -> Any:
    """openpyxl accepts scalars only — anything structured renders as a string.
    Strings are formula-neutralized: openpyxl infers data_type 'f' for a
    leading '=', which would make warehouse data executable on open."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _neutralize(value if isinstance(value, str) else str(value))


def build_report(
    fmt: str, *, sample: dict[str, Any] | None, observed: dict[str, Any] | None
) -> tuple[bytes, str]:
    """(payload bytes, media type) for ``fmt`` — 422 on an unknown format."""
    if fmt == "csv":
        return build_csv(sample, observed), "text/csv"
    if fmt == "xlsx":
        return (
            build_xlsx(sample, observed),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    raise ComparisonReportInvalidError(
        f"unsupported report format {fmt!r}", detail={"supported": list(REPORT_FORMATS)}
    )
