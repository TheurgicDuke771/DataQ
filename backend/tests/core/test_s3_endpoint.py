"""S3-compatible endpoint helpers (#1063) — the shared endpoint/addressing decision.

Pure functions, no boto3: this is the one place the *rules* are asserted, and the
three client sites (`datasources/s3.py`, `datasources/flatfile.py`,
`orchestration/dbt.py`) each assert that they actually apply them.
"""

import pytest

from backend.app.core.s3_endpoint import (
    addressing_config_kwargs,
    normalize_addressing_style,
    normalize_endpoint_url,
    resolve_addressing_style,
)

# ───────────────────────── normalize_endpoint_url ──────────────────


def test_endpoint_url_keeps_a_valid_url() -> None:
    assert normalize_endpoint_url("https://minio.example.com:9000") == (
        "https://minio.example.com:9000"
    )


def test_endpoint_url_strips_trailing_slash_and_whitespace() -> None:
    # A pasted endpoint routinely carries both; without stripping, boto3 would
    # build keys against a double slash.
    assert normalize_endpoint_url("  http://minio:9000/  ") == "http://minio:9000"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_endpoint_url_means_aws(blank: str | None) -> None:
    """Blank must collapse to None, not to the empty string.

    boto3 treats ``endpoint_url=""`` as a real (and broken) endpoint, so a user
    who clears the optional form field would otherwise break a working AWS
    connection rather than reverting it to the default.
    """
    assert normalize_endpoint_url(blank) is None


@pytest.mark.parametrize("bad", ["minio:9000", "s3://bucket", "ftp://host", "//minio:9000"])
def test_endpoint_url_requires_an_http_scheme(bad: str) -> None:
    with pytest.raises(ValueError, match="must start with http:// or https://"):
        normalize_endpoint_url(bad)


# ───────────────────────── addressing style ────────────────────────


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_addressing_style_becomes_auto(blank: str) -> None:
    assert normalize_addressing_style(blank) == "auto"


@pytest.mark.parametrize("value", ["path", "virtual", "auto", "pth", None, 3])
def test_non_blank_addressing_style_passes_through_untouched(value: object) -> None:
    """Only blank is coerced — a typo must still reach the Literal and fail."""
    assert normalize_addressing_style(value) == value


def test_auto_with_an_endpoint_resolves_to_path() -> None:
    """The load-bearing case: MinIO/SeaweedFS serve the bucket in the path only."""
    assert resolve_addressing_style("http://minio:9000", "auto") == "path"


def test_auto_without_an_endpoint_leaves_boto3_alone() -> None:
    """None, not "auto" — the AWS client must be built exactly as it was pre-#1063."""
    assert resolve_addressing_style(None, "auto") is None


@pytest.mark.parametrize("style", ["path", "virtual"])
@pytest.mark.parametrize("endpoint", [None, "http://minio:9000"])
def test_explicit_style_always_wins(style: str, endpoint: str | None) -> None:
    # R2/Wasabi accept virtual-host; a path-style proxy in front of AWS is real too.
    assert resolve_addressing_style(endpoint, style) == style  # type: ignore[arg-type]


def test_config_kwargs_are_empty_for_aws() -> None:
    """Empty dict, so splatting it into `Config(...)` changes nothing at all."""
    assert addressing_config_kwargs(None, "auto") == {}


def test_config_kwargs_carry_the_botocore_shape() -> None:
    assert addressing_config_kwargs("http://minio:9000", "auto") == {
        "s3": {"addressing_style": "path"}
    }
