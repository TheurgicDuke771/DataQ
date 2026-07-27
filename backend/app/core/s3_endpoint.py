"""S3-compatible endpoint configuration, shared by every S3 client DataQ builds.

Three places construct a boto3 S3 client — the S3 datasource adapter
(`datasources/s3.py`), the flat-file read paths (`datasources/flatfile.py`) and
the dbt artifacts poll (`orchestration/dbt.py`) — and a store is only usable if
all of them agree on how to reach it. This module owns that one decision, exactly
as `core/credential_expiry.py` owns credential lifetime for both a datasource
adapter and an orchestration provider.

**Why an endpoint at all (#1063).** MinIO, Ceph/RadosGW, Cloudflare R2, Wasabi,
Backblaze B2, SeaweedFS and on-prem gateways all speak the S3 API; boto3 reaches
any of them by endpoint. Unset, every client resolves the AWS regional endpoint
exactly as it did before — the AWS path is deliberately left byte-identical.
"""

from __future__ import annotations

from typing import Any, Literal

#: Where the bucket goes in a request: in the host (``virtual``,
#: ``<bucket>.<host>/<key>``) or in the path (``path``, ``<host>/<bucket>/<key>``).
#: ``auto`` is DataQ's inference, not botocore's — see `resolve_addressing_style`.
S3AddressingStyle = Literal["auto", "path", "virtual"]


def normalize_endpoint_url(value: str | None) -> str | None:
    """Validate + tidy an S3-compatible endpoint; ``None``/blank means AWS.

    Shared by `S3Config` and `DbtConfig` so an endpoint is accepted in exactly one
    shape wherever it is configured. Mirrors `AdlsConfig._http_url`: the scheme is
    checked here rather than left to boto3, which would otherwise accept
    ``minio:9000`` and fail later with a connection error that says nothing about
    the missing scheme.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.startswith(("http://", "https://")):
        raise ValueError("endpoint_url must start with http:// or https://")
    return stripped.rstrip("/")


def normalize_addressing_style(value: Any) -> Any:
    """Treat a blank addressing style as unset, i.e. ``auto``.

    A ``mode="before"`` coercion, because the field is a `Literal` and ``""``
    would be rejected by it. Blank genuinely reaches here: the connection form
    renders this as an optional text input, so a user who types and then clears it
    submits ``""`` rather than omitting the key — and the same shape arrives from
    the public API and from a suite export/import round-trip. "Left blank" means
    "no preference", which is exactly ``auto``.

    Anything else passes through untouched so a genuine typo still fails loudly
    against the `Literal` rather than being silently coerced to a default.
    """
    if isinstance(value, str) and not value.strip():
        return "auto"
    return value


def resolve_addressing_style(
    endpoint_url: str | None, addressing_style: S3AddressingStyle
) -> str | None:
    """The botocore ``s3.addressing_style``, or ``None`` to leave boto3's default.

    ``auto`` resolves to **path** whenever an endpoint is set. This is load-bearing,
    not a nicety: MinIO and SeaweedFS serve the bucket in the path only, so under
    boto3's default (virtual-host) addressing the client resolves
    ``<bucket>.<host>`` — a name that does not exist — and *every* request fails.
    AWS is unaffected because ``auto`` without an endpoint returns ``None`` here,
    leaving the client constructed exactly as it was before #1063.

    An operator who needs the other behaviour (R2 and Wasabi accept virtual-host;
    a path-style-only proxy in front of AWS is also real) sets ``path``/``virtual``
    explicitly and this passes it straight through.
    """
    if addressing_style != "auto":
        return addressing_style
    return "path" if endpoint_url else None


def addressing_config_kwargs(
    endpoint_url: str | None, addressing_style: S3AddressingStyle
) -> dict[str, Any]:
    """Extra ``botocore.config.Config`` kwargs; ``{}`` leaves the default untouched.

    Each of the three client sites owns its own timeouts and retry policy, so this
    returns the addressing fragment to splat into that site's `Config` rather than
    a whole `Config` — one shared decision, three different transport policies.
    """
    style = resolve_addressing_style(endpoint_url, addressing_style)
    return {"s3": {"addressing_style": style}} if style else {}
