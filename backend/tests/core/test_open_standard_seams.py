"""Telemetry and lineage leave the backend only through their open-standard seams (rule 45)."""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_OTEL_SEAM = {
    "core/otel.py",
    "core/tracing.py",
    "core/logging.py",
    "core/usage_telemetry.py",
}
_VENDOR_EXPORTER_SEAM = {"core/otel.py"}
_LINEAGE_SEAM_DIR = "lineage/"
_PROPRIETARY_TELEMETRY = (
    "opencensus",
    "applicationinsights",
    "ddtrace",
    "datadog",
    "newrelic",
    "sentry_sdk",
    "prometheus_client",
    "statsd",
)


def _imports() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in _APP.rglob("*.py"):
        rel = path.relative_to(_APP).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.append((rel, node.module))
            elif isinstance(node, ast.Import):
                found.extend((rel, alias.name) for alias in node.names)
    return found


def _offenders(prefix: str, allowed: set[str] | None = None, allowed_dir: str = "") -> set[str]:
    return {
        rel
        for rel, module in _imports()
        if (module == prefix or module.startswith(prefix + "."))
        and rel not in (allowed or set())
        and not (allowed_dir and rel.startswith(allowed_dir))
    }


def test_opentelemetry_is_imported_only_by_the_telemetry_seam() -> None:
    assert _offenders("opentelemetry", _OTEL_SEAM) == set()


def test_a_vendor_exporter_is_imported_only_by_the_exporter_seam() -> None:
    assert _offenders("azure.monitor", _VENDOR_EXPORTER_SEAM) == set()


def test_no_proprietary_telemetry_sdk_is_imported() -> None:
    assert set().union(*(_offenders(name) for name in _PROPRIETARY_TELEMETRY)) == set()


def test_openlineage_is_imported_only_by_the_lineage_package() -> None:
    assert _offenders("openlineage", allowed_dir=_LINEAGE_SEAM_DIR) == set()


def test_the_scan_sees_the_seams_it_guards() -> None:
    modules = {module for _, module in _imports()}
    assert any(m.startswith("opentelemetry") for m in modules)
    assert any(m.startswith("openlineage") for m in modules)
