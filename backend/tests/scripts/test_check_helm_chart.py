"""`scripts/check-helm-chart.py` — the Helm chart's invariants, and a broken chart failing each."""

from __future__ import annotations

import copy
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-helm-chart.py"
_SPEC = importlib.util.spec_from_file_location("check_helm_chart", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
hc = importlib.util.module_from_spec(_SPEC)
sys.modules["check_helm_chart"] = hc
_SPEC.loader.exec_module(hc)

_HELM = shutil.which("helm")
needs_helm = pytest.mark.skipif(
    _HELM is None and not os.environ.get("CI"), reason="helm is not installed"
)
_MINIMAL = hc.CI_VALUES / "minimal-values.yaml"


def _meta(name: str, component: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "labels": {"app.kubernetes.io/component": component}, **extra}


def _pod(*containers: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec": {
            "template": {
                "spec": {
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                    "containers": list(containers),
                }
            }
        }
    }


_SAFE = {"allowPrivilegeEscalation": False}


def _good() -> list[dict[str, Any]]:
    def deployment(component: str, command: list[str], **spec: Any) -> dict[str, Any]:
        doc: dict[str, Any] = {"kind": "Deployment", "metadata": _meta(f"d-{component}", component)}
        doc.update(_pod({"name": component, "command": command, "securityContext": _SAFE}))
        doc["spec"].update(spec)
        return doc

    migrate: dict[str, Any] = {
        "kind": "Job",
        "metadata": _meta(
            "d-migrate", "migrate", annotations={"helm.sh/hook": "pre-install,pre-upgrade"}
        ),
    }
    migrate.update(
        _pod(
            {
                "name": "migrate",
                "command": ["alembic", "upgrade", "head"],
                "securityContext": _SAFE,
                "env": [{"name": "SECRET_STORE", "value": "openbao"}],
            }
        )
    )
    migrate["spec"]["backoffLimit"] = 0
    return [
        {
            "kind": "ConfigMap",
            "metadata": _meta("d-env", "config"),
            "data": {"SECRET_STORE": "openbao", "AUTH_EMAIL_PASSWORD_SECRET_NAME": "smtp"},
        },
        {
            "kind": "Service",
            "metadata": _meta("d-api", "api"),
            "spec": {"type": "ClusterIP"},
        },
        {
            "kind": "Service",
            "metadata": _meta("d-frontend", "frontend"),
            "spec": {"type": "LoadBalancer"},
        },
        deployment("api", ["uvicorn"]),
        deployment(
            "worker", ["celery", "-A", "x", "worker", "-Q", "celery,llm", "--loglevel=INFO"]
        ),
        deployment(
            "beat", ["celery", "-A", "x", "beat"], replicas=1, strategy={"type": "Recreate"}
        ),
        migrate,
    ]


def _find(docs: list[dict[str, Any]], kind: str, component: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind and hc._component(d) == component)


def test_a_good_release_has_no_violations() -> None:
    assert hc.violations(_good()) == []


def _container(doc: dict[str, Any]) -> dict[str, Any]:
    container: dict[str, Any] = doc["spec"]["template"]["spec"]["containers"][0]
    return container


def _add_secret(docs: list[dict[str, Any]]) -> None:
    docs.append({"kind": "Secret", "metadata": {"name": "s"}, "data": {"DATABASE_URL": "eA=="}})


def _expose_api(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Service", "api")["spec"]["type"] = "LoadBalancer"


def _drop_api_service(docs: list[dict[str, Any]]) -> None:
    docs.remove(_find(docs, "Service", "api"))


def _ingress_to_api(docs: list[dict[str, Any]]) -> None:
    backend = {"service": {"name": "d-api", "port": {"name": "http"}}}
    docs.append(
        {
            "kind": "Ingress",
            "metadata": {"name": "i"},
            "spec": {"rules": [{"http": {"paths": [{"backend": backend}]}}]},
        }
    )


def _credential_in_configmap(docs: list[dict[str, Any]]) -> None:
    _find(docs, "ConfigMap", "config")["data"]["OPENBAO_TOKEN"] = "x"


def _credential_literal_env(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Job", "migrate"))["env"].append(
        {"name": "DATABASE_URL", "value": "postgresql://u:p@h/db"}
    )


def _no_migrate(docs: list[dict[str, Any]]) -> None:
    docs.remove(_find(docs, "Job", "migrate"))


def _migrate_not_a_hook(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Job", "migrate")["metadata"]["annotations"] = {"helm.sh/hook": "post-install"}


def _migrate_retries(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Job", "migrate")["spec"]["backoffLimit"] = 3


def _migrate_wrong_command(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Job", "migrate"))["command"] = ["alembic", "current"]


def _root_pod(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Deployment", "api")["spec"]["template"]["spec"]["securityContext"] = {}


def _root_container(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Deployment", "worker"))["securityContext"]["runAsUser"] = 0


def _escalation(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Deployment", "beat"))["securityContext"] = {}


def _privileged(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Deployment", "api"))["securityContext"]["privileged"] = True


def _two_beats(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Deployment", "beat")["spec"]["replicas"] = 2


def _beat_rolls(docs: list[dict[str, Any]]) -> None:
    _find(docs, "Deployment", "beat")["spec"]["strategy"] = {"type": "RollingUpdate"}


def _no_beat(docs: list[dict[str, Any]]) -> None:
    docs.remove(_find(docs, "Deployment", "beat"))


def _worker_one_queue(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Deployment", "worker"))["command"] = ["celery", "worker"]


def _worker_embeds_beat(docs: list[dict[str, Any]]) -> None:
    _container(_find(docs, "Deployment", "worker"))["command"].append("-B")


def _no_worker(docs: list[dict[str, Any]]) -> None:
    docs.remove(_find(docs, "Deployment", "worker"))


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        (_add_secret, "never template a Secret"),
        (_expose_api, "only the frontend may be exposed"),
        (_drop_api_service, "api Service must exist"),
        (_ingress_to_api, "routes to d-api"),
        (_credential_in_configmap, "credential-shaped key OPENBAO_TOKEN"),
        (_credential_literal_env, "credential DATABASE_URL set as a literal"),
        (_no_migrate, "no migrate Job"),
        (_migrate_not_a_hook, "pre-install,pre-upgrade hook"),
        (_migrate_retries, "backoffLimit 0"),
        (_migrate_wrong_command, "alembic upgrade head"),
        (_root_pod, "must run as a non-root uid"),
        (_root_container, "must run as a non-root uid"),
        (_escalation, "allowPrivilegeEscalation must be false"),
        (_privileged, "must not be privileged"),
        (_two_beats, "exactly one replica"),
        (_beat_rolls, "Recreate"),
        (_no_beat, "no beat Deployment"),
        (_worker_one_queue, "-Q celery,llm"),
        (_worker_embeds_beat, "must not embed beat"),
        (_no_worker, "no worker Deployment"),
    ],
)
def test_each_breakage_is_reported(breakage: Any, message: str) -> None:
    docs = copy.deepcopy(_good())
    breakage(docs)
    assert any(message in v for v in hc.violations(docs)), hc.violations(docs)


@pytest.mark.parametrize(
    "name",
    [
        "SECRET_STORE",
        "AWS_SECRETS_MANAGER_PREFIX",
        "SECRET_ORPHAN_PURGE",
        "X_SECRET_NAME",
        "TAMPER_ANCHOR_OBJECT_LOCK_ACCESS_KEY_ID",
        "TELEMETRY_SINK",
    ],
)
def test_setting_names_that_merely_mention_secrets_are_not_credentials(name: str) -> None:
    assert not hc._is_credential_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "OPENBAO_TOKEN",
        "OPENBAO_SECRET_ID",
        "TAMPER_ANCHOR_WEBHOOK_SECRET",
        "KV_SECRET_SF",
        "REDIS_URL",
    ],
)
def test_credential_names_are_recognised(name: str) -> None:
    assert hc._is_credential_name(name)


@needs_helm
def test_every_ci_values_file_renders_clean() -> None:
    files = sorted(hc.CI_VALUES.glob("*.yaml"))
    assert len(files) >= 3
    for values in files:
        rendered = hc.render(values)
        assert rendered.returncode == 0, rendered.stderr
        assert hc.violations(hc.parse(rendered.stdout)) == [], values.name


@needs_helm
@pytest.mark.parametrize(("description", "extra"), hc.MUST_FAIL)
def test_the_chart_refuses_bad_values(description: str, extra: list[str]) -> None:
    assert hc.render(_MINIMAL, extra).returncode != 0, description


def _broken_chart(tmp_path: Path, template: str, old: str, new: str) -> Path:
    chart = tmp_path / "dataq"
    shutil.copytree(hc.CHART, chart)
    path = chart / "templates" / template
    text = path.read_text()
    assert old in text, f"{old!r} not in {template}"
    path.write_text(text.replace(old, new, 1))
    return chart


@needs_helm
@pytest.mark.parametrize(
    ("template", "old", "new", "message"),
    [
        (
            "configmap.yaml",
            "apiVersion: v1\nkind: ConfigMap",
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: leak\nstringData:\n"
            "  DATABASE_URL: postgresql://u:p@h/db\n---\napiVersion: v1\nkind: ConfigMap",
            "never template a Secret",
        ),
        ("api.yaml", "  type: ClusterIP", "  type: LoadBalancer", "only the frontend"),
        (
            "migrate-job.yaml",
            "helm.sh/hook: pre-install,pre-upgrade",
            "helm.sh/hook: post-install",
            "pre-install,pre-upgrade hook",
        ),
        ("worker.yaml", '"-Q", "celery,llm", ', "", "-Q celery,llm"),
        (
            "beat.yaml",
            "  strategy:\n    type: Recreate\n",
            "",
            "Recreate",
        ),
    ],
)
def test_a_broken_chart_fails(
    tmp_path: Path, template: str, old: str, new: str, message: str
) -> None:
    chart = _broken_chart(tmp_path, template, old, new)
    rendered = hc.render(_MINIMAL, chart=chart)
    assert rendered.returncode == 0, rendered.stderr
    assert any(message in v for v in hc.violations(hc.parse(rendered.stdout)))


@needs_helm
def test_a_root_backend_fails(tmp_path: Path) -> None:
    chart = _broken_chart(
        tmp_path,
        "_helpers.tpl",
        "{{- toYaml $v.backendSecurityContext.pod | nindent 2 }}",
        "runAsUser: 0",
    )
    rendered = hc.render(_MINIMAL, chart=chart)
    assert rendered.returncode == 0, rendered.stderr
    assert any("non-root uid" in v for v in hc.violations(hc.parse(rendered.stdout)))
