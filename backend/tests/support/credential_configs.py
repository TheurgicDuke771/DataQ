"""Real configs for every adapter whose stored secret is optional, by what they need (#2724).

Each config passes its adapter's own validation (``test_credential_need`` checks), so none is a
hand-made stand-in for what the adapter would accept.
"""

from __future__ import annotations

from typing import Any

_TRINO = {"host": "trino.example.com", "catalog": "hive", "user": "dq"}
_REST = {"catalog_type": "rest", "catalog_uri": "https://catalog.example.com"}

# The config needs no credential at all.
NO_CREDENTIAL_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("gcs", {"bucket": "public-bucket", "auth_type": "anonymous"}),
    ("trino", {**_TRINO, "auth_type": "none"}),
    ("trino", {**_TRINO, "auth_type": "none", "sslmode": "disable"}),
    ("iceberg", dict(_REST)),
    ("iceberg", {"catalog_type": "glue"}),
]

# The config sends the stored secret (`secret_ref`), so it needs one.
CREDENTIAL_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("gcs", {"bucket": "private-bucket", "auth_type": "service_account_key"}),
    ("trino", {**_TRINO, "auth_type": "password"}),
    ("trino", {**_TRINO, "auth_type": "jwt"}),
    ("iceberg", {**_REST, "secret_property": "token"}),
]

# The config needs no `secret_ref` but names a credential stored beside it (a catalog password),
# so there is still one whose acceptance can be observed.
EXTRA_CREDENTIAL_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("iceberg", {**_REST, "catalog_secret_name": "catalog-pw"}),
]
