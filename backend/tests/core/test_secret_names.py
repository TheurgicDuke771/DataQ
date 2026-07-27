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


def test_regex_work_is_bounded_on_hostile_input() -> None:
    """`_PARENS` is polynomial — it rescans to the end from every "(", so N
    unmatched "(" costs O(N^2) (20k chars measured at ~115 ms). The API caps `name`
    at 128, but that makes the safety the CALLER's property, not this function's.
    Asserted as a time bound because that is the actual claim; a length assertion
    would pass even if the bound were removed."""
    import time

    hostile = "(" * 200_000
    start = time.perf_counter()
    ref = connection_secret_ref(
        connection_id=uuid.uuid4(), env="dev", name=hostile, conn_type="snowflake"
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"took {elapsed:.2f}s — the input bound is not being applied"
    assert KEY_VAULT_NAME.match(ref)


@pytest.mark.parametrize(
    ("name", "conn_type", "env", "expected"),
    [
        # Reproduces the names an operator chose BY HAND for prod (+ env and id).
        ("Snowflake — Retail (DATAQ_READER)", "snowflake", "dev", "conn-snowflake-retail-dev"),
        ("Azure Data Factory — QA", "adf", "qa", "conn-adf-qa"),  # type ≠ its words
        ("ADLS — landing (flat files)", "adls_gen2", "dev", "conn-adls-landing-dev"),
        ("Apache Airflow", "airflow", "dev", "conn-airflow-dev"),  # qualifier empties out
        ("Apache Airflow — QA", "airflow", "qa", "conn-airflow-qa"),  # env deduped, not doubled
        ("dbt — Retail Lineage (harness)", "dbt", "dev", "conn-dbt-retail-lineage-dev"),
        ("harness-iceberg", "iceberg", "dev", "conn-iceberg-harness-dev"),
    ],
)
def test_connection_ref_matches_the_curated_prod_convention(
    name: str, conn_type: str, env: str, expected: str
) -> None:
    """The generator only earns its keep by reproducing hand-curated quality.

    Each case is a real production connection whose secret an operator named by
    hand; a naive slug of the display name produced markedly WORSE names
    (`conn-qa-azure-data-factory-qa-…`), which is what this shape fixes.
    """
    ref = connection_secret_ref(
        connection_id=uuid.UUID("05c77ce3-846e-4e76-a5f7-7e12e9510c99"),
        env=env,
        name=name,
        conn_type=conn_type,
    )
    assert ref == f"{expected}-05c77ce3"


def test_type_words_are_not_repeated_in_the_qualifier() -> None:
    """ "Snowflake — Retail" under type `snowflake` must not say snowflake twice."""
    ref = connection_secret_ref(
        connection_id=uuid.uuid4(), env="dev", name="Snowflake Snowflake", conn_type="snowflake"
    )
    assert ref.count("snowflake") == 1


def test_env_is_never_duplicated() -> None:
    ref = connection_secret_ref(
        connection_id=uuid.uuid4(), env="qa", name="Warehouse QA", conn_type="snowflake"
    )
    assert ref.count("qa") == 1


def test_parenthetical_commentary_is_dropped() -> None:
    ref = connection_secret_ref(
        connection_id=uuid.uuid4(), env="dev", name="Retail (DATAQ_READER)", conn_type="snowflake"
    )
    assert "dataq" not in ref and "reader" not in ref


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
    ref = connection_secret_ref(
        connection_id=uuid.uuid4(), env=env, name=name, conn_type="snowflake"
    )
    assert KEY_VAULT_NAME.match(ref), ref
    assert len(ref) <= KEY_VAULT_MAX
    assert not ref.startswith("-") and not ref.endswith("-")
    assert "--" not in ref


def test_connection_ref_survives_a_name_that_slugs_to_nothing() -> None:
    """`日本語` has no ASCII transliteration, so the slug is empty. The ref must
    still be valid and unique rather than collapsing to a bare prefix."""
    cid = uuid.UUID("05c77ce3-846e-4e76-a5f7-7e12e9510c99")
    ref = connection_secret_ref(connection_id=cid, env="dev", name="日本語", conn_type="snowflake")
    assert ref == "conn-snowflake-dev-05c77ce3"
    assert KEY_VAULT_NAME.match(ref)


def test_two_connections_with_the_same_name_get_distinct_refs() -> None:
    """The whole point of the id suffix: identical names must not collide, or one
    connection would silently overwrite the other's credential."""
    a = connection_secret_ref(
        connection_id=uuid.uuid4(), env="dev", name="w", conn_type="snowflake"
    )
    b = connection_secret_ref(
        connection_id=uuid.uuid4(), env="dev", name="w", conn_type="snowflake"
    )
    assert a != b


def test_ref_is_stable_for_the_same_connection() -> None:
    cid = uuid.uuid4()
    first = connection_secret_ref(connection_id=cid, env="dev", name="w", conn_type="snowflake")
    second = connection_secret_ref(connection_id=cid, env="dev", name="w", conn_type="snowflake")
    assert first == second


def test_ref_accepts_a_string_id() -> None:
    """`conn.id` is a UUID object in the ORM but a string in the migration script."""
    cid = "05c77ce3-846e-4e76-a5f7-7e12e9510c99"
    assert connection_secret_ref(
        connection_id=cid, env="dev", name="w", conn_type="snowflake"
    ).endswith("-05c77ce3")


@pytest.mark.parametrize(
    ("ref", "readable"),
    [
        ("conn-05c77ce3-846e-4e76-a5f7-7e12e9510c99", False),  # the legacy shape
        ("conn-snowflake-retail-dev-05c77ce3", True),
        ("conn-adf-qa-05c77ce3", True),
        ("suite-notif-abc", False),  # not a connection ref at all
    ],
)
def test_is_readable_ref_identifies_the_legacy_shape(ref: str, readable: bool) -> None:
    """Drives the migration's idempotency — a second run must skip what it already
    renamed, or it would churn the vault on every invocation."""
    assert is_readable_ref(ref) is readable
