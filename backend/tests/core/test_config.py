"""Tests for Settings-derived config helpers."""

from __future__ import annotations

from pathlib import Path

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


# ── Shipped env templates must actually load (#1072) ───────────────────────── scripts/setup.sh
# copies .env.app.example verbatim to .env.app, and Settings reads env_file=".env.app" by default.

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_env_app_template_plus_setup_sh_values_constructs_settings() -> None:
    """The .env.app that setup.sh actually produces must load into Settings."""
    # These four are what scripts/setup.sh writes into .env.app after copying the template.
    Settings(
        _env_file=str(_REPO_ROOT / ".env.app.example"),
        database_url="postgresql+psycopg2://u:p@localhost:5432/dataq",
        openbao_token="dev-root-token",
        openbao_addr="http://localhost:8200",
        openbao_mount="secret",
    )


def test_blank_valued_template_keys_are_parseable_types() -> None:
    """The same guard one layer down, so a failure names the offending key."""
    offenders: list[str] = []
    for template in (".env.app.example", ".env.example"):
        path = _REPO_ROOT / template
        if not path.exists():  # pragma: no cover - both ship today
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if value.strip():
                continue
            field = Settings.model_fields.get(key.strip().lower())
            if field is None:
                continue  # not a Settings field (compose-only vars live here too)
            annotation = str(field.annotation)
            if "None" in annotation or annotation == "<class 'str'>":
                continue  # "" is a legitimate value for these
            offenders.append(f"{template}:{key.strip()} -> {annotation}")
    assert not offenders, (
        "Blank-valued template keys whose type cannot parse an empty string. "
        "Comment the key out instead of shipping it blank: " + "; ".join(offenders)
    )


def test_env_app_template_llm_reaper_thresholds_match_the_code_default() -> None:
    """#1726 review: unlike most keys the template ships blank, the LLM reaper
    thresholds ship ACTIVE — `setup.sh` copies the template verbatim, so a fresh
    stack loads whatever value is written here, not `Settings`' own default. The
    two drifted once already (the code default was raised to fix a false-kill bug;
    the template kept the old, tight values, silently reintroducing it for every
    new environment). Either side changing alone must fail this test.
    """
    templated = Settings(
        _env_file=str(_REPO_ROOT / ".env.app.example"),
        database_url="postgresql+psycopg2://u:p@localhost:5432/dataq",
        openbao_token="dev-root-token",
        openbao_addr="http://localhost:8200",
        openbao_mount="secret",
    )
    code_default = Settings()
    assert (
        templated.llm_invocation_pending_threshold_minutes
        == code_default.llm_invocation_pending_threshold_minutes
    )
    assert (
        templated.llm_invocation_running_threshold_minutes
        == code_default.llm_invocation_running_threshold_minutes
    )
