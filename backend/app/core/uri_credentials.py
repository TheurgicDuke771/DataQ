"""URI userinfo handling — keep credentials OUT of connection URIs (#754, #826).

A DSN-shaped URI (`postgresql+psycopg2://user:password@host:5432/db`) can smuggle a
password through any field that was only ever meant to hold a *location*. That is
exactly what happened to the Iceberg SQL-catalog `catalog_uri`: the connection type
has one secret slot (used by the storage key), so the catalog DB password ended up
inline in the URI — a *non-secret* config field. From there it was persisted to
`connections.config`, copied verbatim into `assets.namespace` (the OpenLineage
identity), served by the read API, **rendered in the UI**, sent to a third-party
catalog inside a query string, and logged in plaintext.

So these three helpers exist to make that class of bug structurally hard:

- `uri_password` — does this URI carry a password at all? (the create/update guard)
- `strip_uri_credentials` — the safe, *stable* form to persist and to show. Stable
  matters: a namespace derived from a credential-bearing URI **changes when the
  password is rotated**, silently forking the asset into a new identity and
  orphaning its lineage/incidents.
- `inject_uri_password` — put the credential back, at the last possible moment,
  from the SecretStore, only for the live connection.

The username is deliberately **kept**: it's an identifier, not a credential, and
it's part of what makes the URI a stable identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


def uri_password(uri: str) -> str | None:
    """The password embedded in ``uri``'s userinfo, or ``None``.

    Returns ``None`` for a URI with no userinfo *and* for one carrying only a
    username (`scheme://user@host`) — a bare username is not a credential.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    return parts.password or None


def strip_uri_credentials(uri: str) -> str:
    """``uri`` with any userinfo **password** removed, username preserved.

    `postgresql://u:p@h:5432/db?x=1` → `postgresql://u@h:5432/db?x=1`

    Non-URI / unparseable input is returned unchanged: this is used on config that
    may legitimately not be a URI at all (an Iceberg `catalog_uri` can be a bare
    `thrift://host` or even a path), so it must never mangle or raise.
    """
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
    """``uri`` with ``password`` set on its userinfo (the username must already be there).

    The inverse of `strip_uri_credentials`, applied at catalog-load time from the
    SecretStore. The password is **percent-encoded** so a credential containing
    `@`, `:` or `/` can't break out of the userinfo field and silently repoint the
    URI at another host — a URI-injection this function must not enable.

    Returns ``uri`` unchanged when it has no username to attach a password to, or
    when it already carries one (an explicit password in config wins, so this can
    never silently override an operator's intent).
    """
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
    """A copy of ``config`` with any URI-embedded password stripped from string values.

    **Defence in depth, not the fix.** The fix is refusing to store the credential at
    all (`IcebergConfig` rejects a password in `catalog_uri`). But rows written before
    that guard existed still carry one, and `config` is handed to the read API — so
    anything leaving the service is scrubbed on the way out, generically, for every
    connection type. No type-branching: a value is redacted iff it parses as a URI
    that carries a password.
    """
    return {
        k: (strip_uri_credentials(v) if isinstance(v, str) and uri_password(v) else v)
        for k, v in config.items()
    }
