"""Telemetry and lineage leave the backend only through their open-standard seams (rule 45)."""

from __future__ import annotations

import ast
import functools
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_OTEL_SEAM = {
    "core/otel.py",
    "core/tracing.py",
    "core/logging.py",
    "core/usage_telemetry.py",
}
_EXPORTER_SEAM = {"core/otel.py"}
_EXPORTER_PACKAGES = ("opentelemetry.exporter", "azure.monitor")
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
    "elasticapm",
    "logfire",
    "rollbar",
    "bugsnag",
    "honeybadger",
    "aws_xray_sdk",
    "google.cloud.trace",
    "google.cloud.logging",
    "instana",
    "dynatrace",
    "splunk_otel",
    "scout_apm",
    "appdynamics",
)
_DYNAMIC_IMPORTERS = {"import_module", "__import__"}


def _dynamic_import(node: ast.Call) -> str | None:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in _DYNAMIC_IMPORTERS or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


@functools.cache
def _imports() -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for path in _APP.rglob("*.py"):
        rel = path.relative_to(_APP).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.append((rel, node.module))
            elif isinstance(node, ast.Import):
                found.extend((rel, alias.name) for alias in node.names)
            elif isinstance(node, ast.Call) and (module := _dynamic_import(node)):
                found.append((rel, module))
    return tuple(found)


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


def test_every_exporter_is_imported_only_by_the_exporter_seam() -> None:
    assert set().union(*(_offenders(p, _EXPORTER_SEAM) for p in _EXPORTER_PACKAGES)) == set()


def test_no_proprietary_telemetry_sdk_is_imported() -> None:
    assert set().union(*(_offenders(name) for name in _PROPRIETARY_TELEMETRY)) == set()


def test_openlineage_is_imported_only_by_the_lineage_package() -> None:
    assert _offenders("openlineage", allowed_dir=_LINEAGE_SEAM_DIR) == set()


def test_the_scan_sees_the_seams_it_guards() -> None:
    modules = {module for _, module in _imports()}
    assert any(m.startswith("opentelemetry.exporter") for m in modules)
    assert any(m.startswith("openlineage") for m in modules)


def test_a_dynamic_import_is_seen() -> None:
    call = ast.parse("importlib.import_module('sentry_sdk')").body[0].value  # type: ignore[attr-defined]
    assert _dynamic_import(call) == "sentry_sdk"
    call = ast.parse("__import__('ddtrace')").body[0].value  # type: ignore[attr-defined]
    assert _dynamic_import(call) == "ddtrace"
