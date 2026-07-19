"""The local-first posture is a contract, not an aspiration (#591).

After the Azure subscription ends, docker-compose is the only runtime until the
AWS/GCP IaC lands (#505). These tests pin the two ways that posture silently
rots: an env template that quietly starts selecting a cloud implementation, and
an Azure SDK import creeping up to module scope where it would be imported even
when no seam points at Azure.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _template() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (_ROOT / ".env.app.example").read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_env_template_selects_local_implementations() -> None:
    """A fresh clone must run with ZERO cloud configuration. If someone flips the
    template to a cloud default, `setup.sh` starts handing new contributors a
    stack they cannot boot without credentials."""
    values = _template()
    assert values["SECRET_STORE"] == "redis"  # not azure_key_vault
    assert values["AUTH_DEV_BYPASS"] == "true"
    # Every cloud-specific knob ships blank — present for discoverability, unset
    # so nothing reaches for a cloud SDK by default.
    for key in (
        "AZURE_TENANT_ID",
        "AZURE_API_CLIENT_ID",
        "AZURE_KEY_VAULT_URL",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        assert values[key] == "", f"{key} must ship blank in the template"


def test_azure_sdk_is_never_imported_at_module_scope() -> None:
    """Azure is ONE implementation behind a seam (ADR 0010), so its SDK must be
    imported only inside the branch that uses it. A module-scope import makes the
    package a hard dependency of every process — including one that never points
    at Azure — and is how 'provider-agnostic' quietly stops being true."""
    offenders: list[str] = []
    for path in (_ROOT / "backend" / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:  # module scope ONLY — nested imports are the point
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] == "azure" for n in names):
                offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], f"module-scope azure imports: {offenders}"


def test_compose_ships_a_non_azure_telemetry_consumer() -> None:
    """The observability seam needs a working local backend, or 'vendor-neutral'
    is only true on paper. Opt-in via profile: telemetry costs CPU and unset
    endpoint (telemetry off) stays a supported posture."""
    compose = (_ROOT / "docker-compose.yml").read_text()
    assert "jaeger:" in compose
    assert re.search(r'profiles: \["telemetry"\]', compose)


def test_deployment_guide_documents_the_azure_free_path() -> None:
    """AC5: the guide must tell a reader how to run without Azure — the doc IS
    the deliverable for anyone picking this up after the subscription ends."""
    guide = (_ROOT / "docs" / "deployment.md").read_text()
    assert "## Running DataQ without Azure" in guide
    assert "SECRET_STORE=redis" in guide
    assert "AUTH_DEV_BYPASS" in guide
