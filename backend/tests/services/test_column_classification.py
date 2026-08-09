"""Tests for the sample-redaction column classifier (#415)."""

import pytest

from backend.app.services.column_classification import (
    ColumnClass,
    _tokens,
    classify_column,
    is_sensitive,
    value_signal_summary,
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
            "last_name",
            "customer_name",  # bare `name` + no non-person entity → PII
            "date_of_birth",
            "dob",
            "ssn",
            "username",
            "ip_address",  # `address` token, no non-person entity qualifier
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


class TestIssue1182FalsePositives:
    """Regression: the auto-detect heuristic pre-filled non-PII columns (#1182) —
    `rating`/`sentiment` on a feedback-sentiment table and `carrier`/`location_city`
    on a logistics CSV. `rating`/`sentiment`/`carrier` had no token match at all and
    fell through to the conservative-default PII; `location_city` matched the
    legitimate address token `city` with no way to say "this is a place, not a
    person"."""

    @pytest.mark.parametrize(
        "name",
        [
            "rating",
            "sentiment",
            "carrier",
            "location_city",
            "sentiment_score",
            "carrier_name",  # `carrier` is a non-person entity → bare `name` spared too
            "warehouse_zip",
            "carrier_address",
        ],
    )
    def test_observed_false_positives_are_not_pii(self, name: str) -> None:
        assert classify_column(name) is not ColumnClass.PII

    @pytest.mark.parametrize(
        "name",
        [
            "city",  # bare address token, no entity qualifier → still PII (recall)
            "address",
            "customer_city",  # person-qualified → still PII
            "billing_address",
            "home_address",
            "shipping_zip",
        ],
    )
    def test_person_qualified_or_bare_address_stays_pii(self, name: str) -> None:
        # The fix must not regress recall for a genuine person/customer address —
        # only entity-qualified (location_city/warehouse_zip-shaped) columns flip.
        assert classify_column(name) is ColumnClass.PII

    @pytest.mark.parametrize(
        "name",
        [
            "delivery_location_zip",
            "customer_location_address",
            "recipient_location_city",
            "shipping_location_address",
            "pickup_location_zip",
            "customer_location_name",
        ],
    )
    def test_person_context_beats_non_person_entity_qualifier(self, name: str) -> None:
        # Review-caught regression: the entity-qualifier flip (SAFE when an address
        # token or bare `name` is paired with a `_NON_PERSON_ENTITIES` token like
        # `location`) must NOT fire when a person-context token (customer/delivery/
        # shipping/recipient/pickup/…) is also present — `location` alone is
        # ambiguous, and a co-occurring person-context token resolves that ambiguity
        # toward the conservative PII default, not away from it.
        assert classify_column(name) is ColumnClass.PII


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


class TestValueSignalSummary:
    """The persisted full-population counts summary (#1230) — see the module note
    above `_value_signal_counts`. A 20-row capped sample is a much noisier estimator
    of the value-signal ratios than the full population it was capped from; a
    caller with the full, pre-cap values can persist these raw counts so
    `classify_column`/`is_sensitive` can restore the true ratio at read time."""

    def test_basic_counts(self) -> None:
        summary = value_signal_summary(["a@x.com", "b@x.com", "not-an-email", None, ""])
        assert summary == {
            "n": 3,
            "email_count": 2,
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 3,
        }

    def test_none_when_every_value_is_null(self) -> None:
        assert value_signal_summary([None, "", "NULL"]) is None

    def test_classify_column_prefers_summary_email_ratio_over_the_capped_window(self) -> None:
        # `user_id` is an id-NAMED column — the value signal is the ONLY thing that
        # can override an id-shaped name back to PII (the natural-key-holding-emails
        # guard, see `TestValueSignal.test_natural_key_holding_emails_is_pii`). The
        # real population is 60% email (well over the 0.5 PII threshold — a natural
        # key genuinely leaking emails), but ONLY 8 of these 20 sampled/capped values
        # are emails (0.40) — judged on the window alone the override never fires and
        # the column reads "identifier, show it". This is the exact #1230 scenario: a
        # column that's 60% email overall but under 50% in the first 20 rows.
        window = [f"user{i}@x.com" for i in range(8)] + [f"ref-{i}" for i in range(12)]
        assert len(window) == 20
        window_ratio = sum("@" in v for v in window) / len(window)
        assert window_ratio < 0.5  # sanity: the window alone really does disagree

        without_summary = classify_column("user_id", window)
        assert without_summary is ColumnClass.IDENTIFIER  # the bug #1230 describes: shown

        summary = {
            "n": 5_000,
            "email_count": 3_000,  # 60% of the real, pre-cap population
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 5_000,
        }
        with_summary = classify_column("user_id", window, value_signal_summary=summary)
        assert with_summary is ColumnClass.PII  # restored by the full-population summary: masked

    def test_classify_column_summary_corrects_a_low_distinct_hash_column(self) -> None:
        # The real population: 1,000 hash-shaped values but only 100 DISTINCT ones
        # (each repeated 10x) — distinct-ratio 0.1, well under the 0.8 IDENTIFIER
        # threshold, so the real population is correctly PII/masked. A 20-row window
        # that happens to sample 20 distinct hashes reads distinct-ratio 1.0 and
        # flips to IDENTIFIER (shown) — the #1230 "repeated hash column" scenario.
        window = [("a" * 62) + format(i, "02x") for i in range(20)]  # 20 distinct sha256-shaped
        assert len(window) == len(set(window)) == 20

        without_summary = classify_column("token_blob", window)
        assert without_summary is ColumnClass.IDENTIFIER  # the bug: window looks all-distinct

        summary = {
            "n": 1_000,
            "email_count": 0,
            "id_shaped_count": 1_000,
            "encoded_count": 0,
            "distinct_count": 100,  # 0.1 distinct-ratio over the real population
        }
        with_summary = classify_column("token_blob", window, value_signal_summary=summary)
        assert with_summary is ColumnClass.PII  # restored: the real population repeats heavily

    def test_is_sensitive_prefers_the_summary_too(self) -> None:
        window = [f"user{i}@x.com" for i in range(8)] + [f"ref-{i}" for i in range(12)]
        summary = {
            "n": 5_000,
            "email_count": 3_000,
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 5_000,
        }
        assert is_sensitive("ext_val", window) is False
        assert is_sensitive("ext_val", window, value_signal_summary=summary) is True

    def test_classify_column_falls_back_when_the_summary_is_malformed(self) -> None:
        # A corrupt/hand-edited summary (missing keys, zero population) must not
        # crash or be trusted — it falls back to deriving from `sampled_values`
        # exactly as if no summary had been given at all (old-row compatibility).
        window = ["ref-1", "ref-2", "ref-3"]
        baseline = classify_column("ext_val", window)
        assert classify_column("ext_val", window, value_signal_summary={"n": 0}) == baseline
        assert (
            classify_column("ext_val", window, value_signal_summary={"n": "not-an-int"}) == baseline
        )
        assert classify_column("ext_val", window, value_signal_summary=None) == baseline

    def test_classify_column_with_no_summary_and_no_values_stays_the_conservative_default(
        self,
    ) -> None:
        assert classify_column("ext_val", None, value_signal_summary=None) is ColumnClass.PII

    def test_classify_column_rejects_a_summary_with_a_sub_count_over_n(self) -> None:
        # Review finding: `_classify_counts` divides each sub-count by `n` unguarded,
        # so an internally-inconsistent summary (corrupted JSONB, a future writer
        # bug, a hand-edited row) with a sub-count INFLATED past `n` must be rejected
        # rather than trusted — otherwise a bogus id_shaped_count/distinct_count
        # both > n forces a >=0.8 ratio no matter what the real population looked
        # like, which could flip a genuinely-PII column to IDENTIFIER and show it.
        window = ["ref-1", "ref-2", "ref-3"]
        baseline = classify_column("ext_val", window)
        bogus = {
            "n": 1_000,
            "email_count": 0,
            "id_shaped_count": 2_000,  # > n — internally inconsistent
            "encoded_count": 0,
            "distinct_count": 2_000,  # > n — internally inconsistent
        }
        assert classify_column("ext_val", window, value_signal_summary=bogus) == baseline
