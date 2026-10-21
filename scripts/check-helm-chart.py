#!/usr/bin/env python3
"""Lint and render the Helm chart against each values file under its ci/ directory, and hold
every render to the chart's invariants. Exits non-zero on the first file with a violation.

    python scripts/check-helm-chart.py [--kubeconform PATH]

Needs `helm` on PATH (v3 or v4).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "dataq"
CI_VALUES = CHART / "ci"
CREDENTIAL_NAME = re.compile(
    r"PASSWORD|TOKEN|(^|_)SECRET(_ID)?$|^KV_SECRET_|_KEY$|CONNECTION_STRING|^DATABASE_URL$|^REDIS_URL$"
)
WORKLOAD_KINDS = {"Deployment", "Job"}

# Values the chart must refuse at install time: (description, extra helm args).
MUST_FAIL: tuple[tuple[str, list[str]], ...] = (
    ("no existingSecret", ["--set", "existingSecret="]),
    ("dev bypass without the opt-in", ["--set", "auth.mode=bypass"]),
    (
        "dev bypass outside ENVIRONMENT=dev",
        ["--set", "auth.mode=bypass,auth.allowDevBypass=true"],
    ),
    ("a credential in config.extraEnv", ["--set", "config.extraEnv.OPENBAO_TOKEN=x"]),
    ("the moving latest tag", ["--set", "image.backend.tag=latest"]),
    ("a root pod", ["--set", "backendSecurityContext.pod.runAsNonRoot=false"]),
    ("an unknown auth mode", ["--set", "auth.mode=saml"]),
    ("a telemetry file with nowhere to write it", ["--set", "config.telemetry.sink=file"]),
    ("an unknown top-level key", ["--set", "apii.replicas=2"]),
)


def _is_credential_name(name: str) -> bool:
    upper = name.upper()
    return bool(CREDENTIAL_NAME.search(upper)) and not upper.endswith("_NAME")


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec: dict[str, Any] = doc["spec"]["template"]["spec"]
    return spec


def _containers(pod: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from pod.get("initContainers") or []
    yield from pod.get("containers") or []


def _name(doc: dict[str, Any]) -> str:
    return f"{doc.get('kind')}/{doc.get('metadata', {}).get('name')}"


def _component(doc: dict[str, Any]) -> str | None:
    labels = doc.get("metadata", {}).get("labels") or {}
    value = labels.get("app.kubernetes.io/component")
    return str(value) if value is not None else None


def violations(docs: list[dict[str, Any]]) -> list[str]:
    """Every invariant breach in one rendered release."""
    found: list[str] = []
    by_component: dict[tuple[str, str | None], dict[str, Any]] = {}
    for doc in docs:
        by_component.setdefault((doc["kind"], _component(doc)), doc)

    for doc in docs:
        kind = doc["kind"]
        if kind == "Secret":
            found.append(f"{_name(doc)}: the chart must never template a Secret")
        if kind == "ConfigMap":
            for key in doc.get("data") or {}:
                if _is_credential_name(key):
                    found.append(f"{_name(doc)}: credential-shaped key {key} in a ConfigMap")
        if kind == "Service" and _component(doc) != "frontend":
            if doc["spec"].get("type", "ClusterIP") != "ClusterIP":
                found.append(f"{_name(doc)}: only the frontend may be exposed")
        if kind == "Ingress":
            for rule in doc["spec"].get("rules") or []:
                for path in rule.get("http", {}).get("paths") or []:
                    service = path["backend"]["service"]["name"]
                    if not service.endswith("-frontend"):
                        found.append(f"{_name(doc)}: routes to {service}, not the frontend")
        if kind in WORKLOAD_KINDS:
            found.extend(_workload_violations(doc))

    api_svc = by_component.get(("Service", "api"))
    if api_svc is None or api_svc["spec"].get("type", "ClusterIP") != "ClusterIP":
        found.append("the api Service must exist and be ClusterIP")

    migrate = by_component.get(("Job", "migrate"))
    if migrate is None:
        found.append("no migrate Job")
    else:
        hooks = set(
            str(migrate["metadata"].get("annotations", {}).get("helm.sh/hook", "")).split(",")
        )
        if not {"pre-install", "pre-upgrade"} <= hooks:
            found.append("the migrate Job must be a pre-install,pre-upgrade hook")
        if migrate["spec"].get("backoffLimit") != 0:
            found.append("the migrate Job must have backoffLimit 0")
        command = _pod_spec(migrate)["containers"][0].get("command") or []
        if command[-3:] != ["alembic", "upgrade", "head"]:
            found.append("the migrate Job must run alembic upgrade head")

    beat = by_component.get(("Deployment", "beat"))
    if beat is None:
        found.append("no beat Deployment")
    else:
        if beat["spec"].get("replicas") != 1:
            found.append("beat must run exactly one replica")
        if beat["spec"].get("strategy", {}).get("type") != "Recreate":
            found.append("beat must roll with the Recreate strategy")

    worker = by_component.get(("Deployment", "worker"))
    if worker is None:
        found.append("no worker Deployment")
    else:
        command = _pod_spec(worker)["containers"][0].get("command") or []
        if "-Q" not in command or command[command.index("-Q") + 1] != "celery,llm":
            found.append("the worker must consume -Q celery,llm")
        if "-B" in command or "--beat" in command:
            found.append("the worker must not embed beat")
    return found


def _workload_violations(doc: dict[str, Any]) -> list[str]:
    found: list[str] = []
    pod = _pod_spec(doc)
    pod_ctx = pod.get("securityContext") or {}
    for container in _containers(pod):
        where = f"{_name(doc)} container {container['name']}"
        ctx = container.get("securityContext") or {}
        non_root = ctx.get("runAsNonRoot", pod_ctx.get("runAsNonRoot"))
        uid = ctx.get("runAsUser", pod_ctx.get("runAsUser"))
        if non_root is not True or uid in (None, 0):
            found.append(f"{where}: must run as a non-root uid")
        if ctx.get("allowPrivilegeEscalation") is not False:
            found.append(f"{where}: allowPrivilegeEscalation must be false")
        if ctx.get("privileged"):
            found.append(f"{where}: must not be privileged")
        for env in container.get("env") or []:
            if "value" in env and _is_credential_name(env["name"]):
                found.append(f"{where}: credential {env['name']} set as a literal value")
    return found


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603
        argv, capture_output=True, text=True, check=False
    )


def render(
    values: Path, extra: list[str] | None = None, chart: Path = CHART
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["helm", "template", "dataq", str(chart), "--namespace", "dataq", "-f", str(values)]
        + (extra or [])
    )


def parse(manifest: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(manifest) if doc]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kubeconform", help="path to kubeconform; validates every render")
    args = parser.parse_args()
    if shutil.which("helm") is None:
        print("helm is not on PATH", file=sys.stderr)
        return 2

    values_files = sorted(CI_VALUES.glob("*.yaml"))
    if not values_files:
        print(f"no values files under {CI_VALUES}", file=sys.stderr)
        return 2
    failed = False
    for values in values_files:
        label = values.relative_to(ROOT)
        lint = _run(["helm", "lint", "--strict", str(CHART), "-f", str(values)])
        if lint.returncode != 0:
            print(f"FAIL helm lint {label}\n{lint.stdout}{lint.stderr}")
            failed = True
            continue
        rendered = render(values)
        if rendered.returncode != 0:
            print(f"FAIL helm template {label}\n{rendered.stderr}")
            failed = True
            continue
        problems = violations(parse(rendered.stdout))
        if args.kubeconform:
            conform = subprocess.run(  # noqa: S603  # nosec B603
                [args.kubeconform, "-strict", "-summary", "-"],
                input=rendered.stdout,
                capture_output=True,
                text=True,
                check=False,
            )
            if conform.returncode != 0:
                problems.append(f"kubeconform: {conform.stdout}{conform.stderr}")
        for problem in problems:
            print(f"FAIL {label}: {problem}")
        failed = failed or bool(problems)
        if not problems:
            print(f"ok   {label}")

    baseline = values_files[0]
    for description, extra in MUST_FAIL:
        if render(baseline, extra).returncode == 0:
            print(f"FAIL the chart accepted {description}")
            failed = True
        else:
            print(f"ok   refuses {description}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
