"""S3-compatible endpoint configuration, shared by every S3 client DataQ builds."""

from __future__ import annotations

from typing import Any, Literal

from backend.app.core.uri_credentials import uri_password

#: Where the bucket goes in a request: in the host (``virtual``, ``<bucket>.<host>/<key>``) or in
#: the path (``path``, ``<host>/<bucket>/<key>``).
S3AddressingStyle = Literal["auto", "path", "virtual"]


def normalize_endpoint_url(value: str | None) -> str | None:
    """Validate + tidy an S3-compatible endpoint; ``None``/blank means AWS."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.startswith(("http://", "https://")):
        raise ValueError("endpoint_url must start with http:// or https://")
    # Refuse a credential smuggled into the URL, the settled rule for a URL-shaped *non-secret*
    # config field (#754/#826, and `IcebergConfig._uri_carries_no_password` for the precedent).
    if uri_password(stripped):
        raise ValueError(
            "endpoint_url must not embed a credential (config is stored and returned "
            "in plaintext). Put the access key id in 'access_key_id' and the secret "
            "access key in the secret store."
        )
    return stripped.rstrip("/")


def normalize_addressing_style(value: Any) -> Any:
    """Treat a blank addressing style as unset, i.e. ``auto``."""
    if isinstance(value, str) and not value.strip():
        return "auto"
    return value


def resolve_addressing_style(
    endpoint_url: str | None, addressing_style: S3AddressingStyle
) -> str | None:
    """The botocore ``s3.addressing_style``, or ``None`` to leave boto3's default."""
    if addressing_style != "auto":
        return addressing_style
    return "path" if endpoint_url else None


def addressing_config_kwargs(
    endpoint_url: str | None, addressing_style: S3AddressingStyle
) -> dict[str, Any]:
    """Extra ``botocore.config.Config`` kwargs; ``{}`` leaves the default untouched."""
    style = resolve_addressing_style(endpoint_url, addressing_style)
    return {"s3": {"addressing_style": style}} if style else {}
