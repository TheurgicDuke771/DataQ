"""URI credential handling (#754, #826) — the helpers that keep passwords out of config,
out of the asset identity, and out of anything the API hands back."""

from __future__ import annotations

import pytest

from backend.app.core.uri_credentials import (
    inject_uri_password,
    redact_config_uris,
    strip_uri_credentials,
    uri_password,
)

_DSN = (
    "postgresql+psycopg2://airflowadmin:s3cr3t@pg.example.com:5432/iceberg_catalog?sslmode=require"
)
_CLEAN = "postgresql+psycopg2://airflowadmin@pg.example.com:5432/iceberg_catalog?sslmode=require"


class TestUriPassword:
    def test_finds_the_password(self) -> None:
        assert uri_password(_DSN) == "s3cr3t"

    def test_a_bare_username_is_not_a_credential(self) -> None:
        # `scheme://user@host` is an identifier, not a secret — must not be flagged,
        # or the create-guard would reject every legitimate credential-less URI.
        assert uri_password(_CLEAN) is None

    @pytest.mark.parametrize("uri", ["thrift://hive:9083", "file", "", "not a uri at all"])
    def test_non_credential_uris(self, uri: str) -> None:
        assert uri_password(uri) is None


class TestStrip:
    def test_removes_the_password_and_keeps_everything_else(self) -> None:
        # Host, port, path AND query must survive — a mangled DSN would break the
        # catalog connection just as surely as a leaked one.
        assert strip_uri_credentials(_DSN) == _CLEAN

    def test_is_idempotent(self) -> None:
        assert strip_uri_credentials(_CLEAN) == _CLEAN

    @pytest.mark.parametrize("uri", ["thrift://hive:9083", "file", "s3://bucket/path"])
    def test_passes_non_credential_uris_through_untouched(self, uri: str) -> None:
        # catalog_uri is not always a DSN (it can be a bare host or even a path) —
        # this must never mangle or raise on those.
        assert strip_uri_credentials(uri) == uri


class TestInject:
    def test_round_trips_with_strip(self) -> None:
        assert inject_uri_password(strip_uri_credentials(_DSN), "s3cr3t") == _DSN

    def test_percent_encodes_so_a_password_cannot_break_out_of_the_userinfo(self) -> None:
        # A password containing @ : / would otherwise re-point the URI at another
        # host — a URI-injection. Assert the dangerous characters are escaped AND
        # that the real host survives.
        out = inject_uri_password("postgresql://u@real-host:5432/db", "p@ss:w/rd")
        assert "@real-host:5432/db" in out
        assert "p%40ss%3Aw%2Frd" in out
        assert out.count("@") == 1  # exactly the userinfo separator

    def test_never_overrides_an_explicit_password(self) -> None:
        assert inject_uri_password(_DSN, "other") == _DSN

    def test_no_username_means_nothing_to_attach_to(self) -> None:
        assert inject_uri_password("thrift://hive:9083", "pw") == "thrift://hive:9083"


class TestRedactConfig:
    def test_scrubs_a_credential_bearing_uri_anywhere_in_config(self) -> None:
        out = redact_config_uris({"catalog_uri": _DSN, "warehouse": "abfss://w@a.dfs/x"})
        assert out["catalog_uri"] == _CLEAN
        assert "s3cr3t" not in str(out)

    def test_leaves_non_uri_values_alone(self) -> None:
        cfg = {"catalog_name": "default", "properties": {"k": "v"}, "port": 5432}
        assert redact_config_uris(cfg) == cfg
