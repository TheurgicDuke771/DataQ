"""`check_service.validate_threshold_ordering` tests (#568).

Pure — no DB. `derive_status` (severity.py) assumes thresholds are ordered
warn <= fail <= critical and non-negative; this is the author-time guard that
enforces it before a bad set ever reaches that assumption. Shared by
`create_check`, `update_check`, `suite_io_service.import_suite`, and the
check-editor dry-run preview (`dryrun_service.dry_run_check`) — those wiring
paths are exercised at the API layer in `tests/api/test_checks.py` and
`tests/api/test_suites.py`; this file covers the validator itself.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.services.check_service import (
    CheckConfigInvalidError,
    validate_threshold_ordering,
)


def _d(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


# ── valid orderings — must not raise ──


@pytest.mark.parametrize(
    ("warn", "fail", "critical"),
    [
        (None, None, None),  # no thresholds at all
        ("1", "5", "20"),  # strictly ascending
        ("5", "5", "5"),  # all equal — boundary, not "inverted"
        ("1", "5", None),  # critical unset
        ("1", None, "20"),  # fail unset (skips straight to comparing warn<=critical)
        (None, "5", "20"),  # warn unset
        ("1", None, None),  # only warn set
        (None, "5", None),  # only fail set
        (None, None, "20"),  # only critical set
        ("0", "0", "0"),  # zero is non-negative, not invalid
    ],
)
def test_valid_orderings_pass(warn: str | None, fail: str | None, critical: str | None) -> None:
    validate_threshold_ordering(
        warn_threshold=_d(warn), fail_threshold=_d(fail), critical_threshold=_d(critical)
    )  # must not raise


# ── inverted orderings — 422 ──


@pytest.mark.parametrize(
    ("warn", "fail", "critical"),
    [
        ("90", "50", "10"),  # fully inverted (the issue's example)
        ("10", "5", None),  # warn > fail, critical unset
        (None, "50", "10"),  # fail > critical, warn unset
        ("20", None, "10"),  # warn > critical, fail unset (skip-a-threshold case)
        ("5", "10", "8"),  # warn <= fail, but fail > critical
    ],
)
def test_inverted_orderings_rejected(
    warn: str | None, fail: str | None, critical: str | None
) -> None:
    with pytest.raises(CheckConfigInvalidError):
        validate_threshold_ordering(
            warn_threshold=_d(warn), fail_threshold=_d(fail), critical_threshold=_d(critical)
        )


# ── negative thresholds — 422, regardless of ordering ──


@pytest.mark.parametrize(
    ("warn", "fail", "critical"),
    [
        ("-1", None, None),
        (None, "-5", None),
        (None, None, "-0.01"),
        ("-10", "-5", "-1"),  # negative but internally ordered — sign still rejected
    ],
)
def test_negative_thresholds_rejected(
    warn: str | None, fail: str | None, critical: str | None
) -> None:
    with pytest.raises(CheckConfigInvalidError):
        validate_threshold_ordering(
            warn_threshold=_d(warn), fail_threshold=_d(fail), critical_threshold=_d(critical)
        )
