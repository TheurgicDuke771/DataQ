"""Tests for the sample-redaction column classifier (#415)."""

import pytest

from backend.app.services.column_classification import (
    ColumnClass,
    _tokens,
    classify_column,
)


class TestTokeniser:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ORDER_NUMBER", ["order", "number"]),
            ("customerEmail", ["customer", "email"]),
            ("customer-id", ["customer", "id"]),
            ("load_ts", ["load", "ts"]),
            ("SKU", ["sku"]),
            ("", []),
        ],
    )
    def test_splits_snake_camel_kebab(self, name: str, expected: list[str]) -> None:
        assert _tokens(name) == expected


class TestNameSignal:
    @pytest.mark.parametrize(
        "name",
        [
            "email",
            "customer_email",
            "home_address",
            "phone_number",  # phone (person) wins over number (id)
            "first_name",
            "customer_name",  # bare `name` + no non-person entity → PII
            "date_of_birth",
            "ssn",
            "username",
        ],
    )
    def test_person_columns_are_pii(self, name: str) -> None:
        assert classify_column(name) is ColumnClass.PII

    @pytest.mark.parametrize(
        "name",
        [
            "customer_id",  # surrogate/pseudonymous key → the ideal row locator
            "user_id",
            "account_id",
            "member_key",
            "order_number",
            "order_id",
            "sku",
            "sku_id",
            "product_id",
            "tracking_number",
            "invoice_no",
            "transaction_id",
            "batch_id",
        ],
    )
    def test_ids_are_identifiers(self, name: str) -> None:
        # Person-linking surrogate keys (customer_id/user_id) are locators too —
        # opaque, don't leak a direct identifier — so they're shown, not masked.
        assert classify_column(name) is ColumnClass.IDENTIFIER

    @pytest.mark.parametrize(
        "name",
        [
            "load_ts",
            "order_ts",
            "created_at",
            "line_total",
            "unit_price",
            "quantity",
            "status",
            "channel",
            "on_hand",  # unknown token → falls through; see default test
        ],
    )
    def test_metric_time_status_are_safe_or_default(self, name: str) -> None:
        # These are all either SAFE (known metric/time/status token) or the
        # conservative default; none should be a shown IDENTIFIER.
        assert classify_column(name) is not ColumnClass.IDENTIFIER

    @pytest.mark.parametrize(
        "name",
        [
            "account_number",
            "account_no",
            "card_number",
            "credit_card_number",
            "cc_number",
            "card_no",
            "tax_id",
            "national_id",
            "vat_number",
            "routing_number",
            "iban",
            "swift",
            "cvv",
        ],
    )
    def test_sensitive_domain_identifiers_are_pii(self, name: str) -> None:
        # A financial/national identifier is direct PII — the id-suffix must NOT make it
        # a shown locator (regression: account_number/card_number/tax_id leaked).
        assert classify_column(name) is ColumnClass.PII

    @pytest.mark.parametrize(
        "name",
        ["tax_amount", "account_status", "tax_rate", "account_id", "card_id"],
    )
    def test_sensitive_domain_non_numbers_stay_non_pii(self, name: str) -> None:
        # A domain value/label (tax_amount, account_status) or a surrogate FK
        # (account_id/card_id) is NOT the sensitive number → not masked as PII.
        assert classify_column(name) is not ColumnClass.PII

    def test_product_name_is_not_pii(self) -> None:
        # `name` labelling a non-person entity must NOT be treated as personal.
        assert classify_column("product_name") is not ColumnClass.PII
        assert classify_column("category_name") is not ColumnClass.PII

    def test_safe_tokens_classified_safe(self) -> None:
        assert classify_column("load_ts") is ColumnClass.SAFE
        assert classify_column("line_total") is ColumnClass.SAFE
        assert classify_column("status") is ColumnClass.SAFE


class TestValueSignal:
    def test_uuid_values_are_identifier_when_name_unknown(self) -> None:
        uuids = [
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "3f333df6-90a4-4fda-8dd3-9485d27cee36",
            "7d444840-9dc0-11d1-b245-5ffdce74fad2",
        ]
        assert classify_column("ext_ref_val", uuids) is ColumnClass.IDENTIFIER

    def test_hash_values_are_identifier_when_name_unknown(self) -> None:
        sha256 = [
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
            "486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7",
        ]
        assert classify_column("token_blob", sha256) is ColumnClass.IDENTIFIER

    def test_high_entropy_encoded_blobs_are_pii(self) -> None:
        blobs = [
            "U2FsdGVkX1+9Kj3lm8Qz7wZ2h6vN3pQ==",
            "aGVsbG8gd29ybGQgdGhpcyBpcyBiYXNlNjQ=",
            "Zm9vYmFyYmF6cXV4c2VjcmV0dmFsdWVoZXJl",
        ]
        assert classify_column("blob_col", blobs) is ColumnClass.PII

    def test_name_beats_values(self) -> None:
        # A PII-named column stays PII even if its (hashed) values look id-shaped.
        hashed = ["d41d8cd98f00b204e9800998ecf8427e"] * 3
        assert classify_column("email", hashed) is ColumnClass.PII

    def test_natural_key_holding_emails_is_pii(self) -> None:
        # An id-NAMED column whose values are emails is a natural key leaking a direct
        # identifier — the value signal must override the name → PII (not shown).
        emails = ["ada@acme.io", "bo@acme.io", "cy@acme.io", "di@acme.io"]
        assert classify_column("user_id", emails) is ColumnClass.PII

    def test_surrogate_key_with_opaque_values_stays_identifier(self) -> None:
        # customer_id with opaque integer/coded values → the ideal locator, shown.
        assert classify_column("customer_id", [4471, 8823, 91, 20455]) is ColumnClass.IDENTIFIER
        assert classify_column("customer_id", ["CUST-0001", "CUST-0002"]) is ColumnClass.IDENTIFIER


class TestDefault:
    def test_unknown_name_no_values_defaults_pii(self) -> None:
        assert classify_column("wibble") is ColumnClass.PII

    def test_unknown_name_plain_values_defaults_pii(self) -> None:
        # Non-id-shaped, low-entropy values leave it unknown → conservative mask.
        assert classify_column("wibble", ["a", "b", "a", "c"]) is ColumnClass.PII

    def test_null_only_values_fall_back_to_name(self) -> None:
        assert classify_column("order_number", [None, "NULL", ""]) is ColumnClass.IDENTIFIER
