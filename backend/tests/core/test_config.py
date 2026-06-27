"""Tests for Settings-derived config helpers."""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("https://app.example.com", ["https://app.example.com"]),
        ("https://a.io, https://b.io", ["https://a.io", "https://b.io"]),
        (
            "  https://a.io ,, https://b.io  ",
            ["https://a.io", "https://b.io"],
        ),  # trim + drop empties
    ],
)
def test_cors_allow_origin_list(raw: str, expected: list[str]) -> None:
    assert Settings(cors_allow_origins=raw).cors_allow_origin_list == expected


def test_cors_off_by_default() -> None:
    # No origins configured → empty list → CORS middleware stays off.
    assert Settings().cors_allow_origin_list == []
