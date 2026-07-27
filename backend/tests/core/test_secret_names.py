"""Readable SecretStore key names.

The charset assertions are the load-bearing ones: Azure Key Vault rejects any
name outside ``[0-9a-zA-Z-]`` at the API, so a slug that lets one through does
not fail in a test — it fails when an operator saves a connection in production.
"""

from __future__ import annotations

import re
import uuid

import pytest

from backend.app.core.secret_names import connection_secret_ref, is_readable_ref, slugify

# Azure Key Vault's own rule — the tightest of the backends behind the seam.
KEY_VAULT_NAME = re.compile(r"^[0-9a-zA-Z-]+$")
KEY_VAULT_MAX = 127


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("finance warehouse", "finance-warehouse"),
        ("Finance Warehouse", "finance-warehouse"),
        ("finance_warehouse", "finance-warehouse"),  # underscore is illegal in Key Vault
        ("finance.warehouse", "finance-warehouse"),  # so is a dot
        ("finance/warehouse", "finance-warehouse"),  # and a slash would nest the KV v2 path
        ("  finance  ", "finance"),
        ("finance---warehouse", "finance-warehouse"),  # runs collapse
        ("--finance--", "finance"),  # no leading/trailing dashes
        ("Ünïcodé Wärehouse", "unicode-warehouse"),  # transliterated, not dropped
        ("snowflake (prod) #1", "snowflake-prod-1"),
    ],
)
def test_slugify_produces_readable_key_vault_safe_names(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "finance warehouse",
        "Ünïcodé",
        "!!!",
        "",
        "a" * 500,
        "日本語",  # no ASCII transliteration exists
        "../../etc/passwd",
        "conn/../other",
    ],
)
def test_slugify_output_is_always_key_vault_writable(raw: str) -> None:
    """Including the cases that slug to nothing — an empty string is valid output
    here, but anything non-empty must be writable."""
    slug = slugify(raw)
    assert slug == "" or KEY_VAULT_NAME.match(slug), f"{raw!r} -> {slug!r}"


def test_slugify_is_bounded() -> None:
    assert len(slugify("a" * 500)) <= 60


def test_connection_ref_is_readable() -> None:
    ref = connection_secret_ref(
        connection_id=uuid.UUID("05c77ce3-846e-4e76-a5f7-7e12e9510c99"),
        env="dev",
        name="Finance Warehouse",
    )
    assert ref == "conn-dev-finance-warehouse-05c77ce3"


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ("dev", "finance warehouse"),
        ("prod", "Ünïcodé"),
        ("uat", "!!!"),
        ("dev", ""),
        ("dev", "a" * 500),
        ("dev", "日本語"),
        ("dev", "../../etc/passwd"),
    ],
)
def test_connection_ref_is_always_writable_to_key_vault(env: str, name: str) -> None:
    """A connection name is free text straight from the user. Every one of these
    must yield a name Key Vault will accept — the failure mode otherwise is a
    500 on save, in production, for a name nobody thought to test."""
    ref = connection_secret_ref(connection_id=uuid.uuid4(), env=env, name=name)
    assert KEY_VAULT_NAME.match(ref), ref
    assert len(ref) <= KEY_VAULT_MAX
    assert not ref.startswith("-") and not ref.endswith("-")
    assert "--" not in ref


def test_connection_ref_survives_a_name_that_slugs_to_nothing() -> None:
    """`日本語` has no ASCII transliteration, so the slug is empty. The ref must
    still be valid and unique rather than collapsing to a bare prefix."""
    cid = uuid.UUID("05c77ce3-846e-4e76-a5f7-7e12e9510c99")
    ref = connection_secret_ref(connection_id=cid, env="dev", name="日本語")
    assert ref == "conn-dev-05c77ce3"
    assert KEY_VAULT_NAME.match(ref)


def test_two_connections_with_the_same_name_get_distinct_refs() -> None:
    """The whole point of the id suffix: identical names must not collide, or one
    connection would silently overwrite the other's credential."""
    a = connection_secret_ref(connection_id=uuid.uuid4(), env="dev", name="warehouse")
    b = connection_secret_ref(connection_id=uuid.uuid4(), env="dev", name="warehouse")
    assert a != b


def test_ref_is_stable_for_the_same_connection() -> None:
    cid = uuid.uuid4()
    first = connection_secret_ref(connection_id=cid, env="dev", name="warehouse")
    second = connection_secret_ref(connection_id=cid, env="dev", name="warehouse")
    assert first == second


def test_ref_accepts_a_string_id() -> None:
    """`conn.id` is a UUID object in the ORM but a string in the migration script."""
    cid = "05c77ce3-846e-4e76-a5f7-7e12e9510c99"
    assert connection_secret_ref(connection_id=cid, env="dev", name="w").endswith("-05c77ce3")


@pytest.mark.parametrize(
    ("ref", "readable"),
    [
        ("conn-05c77ce3-846e-4e76-a5f7-7e12e9510c99", False),  # the legacy shape
        ("conn-dev-finance-warehouse-05c77ce3", True),
        ("conn-dev-05c77ce3", True),
        ("suite-notif-abc", False),  # not a connection ref at all
    ],
)
def test_is_readable_ref_identifies_the_legacy_shape(ref: str, readable: bool) -> None:
    """Drives the migration's idempotency — a second run must skip what it already
    renamed, or it would churn the vault on every invocation."""
    assert is_readable_ref(ref) is readable
