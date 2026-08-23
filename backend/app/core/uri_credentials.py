"""URI userinfo handling — keep credentials OUT of connection URIs (#754, #826)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


def uri_password(uri: str) -> str | None:
    """The password embedded in ``uri``'s userinfo, or ``None``."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    return parts.password or None


def strip_uri_credentials(uri: str) -> str:
    """``uri`` with any userinfo **password** removed, username preserved."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    if not parts.password:
        return uri

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def inject_uri_password(uri: str, password: str) -> str:
    """``uri`` with ``password`` set on its userinfo (the username must already be there)."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    if not parts.username or parts.password:
        return uri

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{quote(parts.username, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_config_uris(config: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``config`` with any URI-embedded password stripped from string values."""
    return {
        k: (strip_uri_credentials(v) if isinstance(v, str) and uri_password(v) else v)
        for k, v in config.items()
    }
