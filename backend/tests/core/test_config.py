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


# ── Shipped env templates must actually load (#1072) ─────────────────────────
#
# scripts/setup.sh copies .env.app.example verbatim to .env.app, and Settings
# reads env_file=".env.app" by default — so an unloadable template breaks the
# documented from-scratch install. Nothing checked this, which is how a blank
# bool sat in the template for two weeks (#1072, introduced by #776).
#
# The failure is specifically a PRESENT-BUT-EMPTY value on a field whose type
# cannot parse "": `str | None` shrugs at it, `bool` raises. So the guard is
# "construct Settings from the template", not "grep for blank keys" — the
# former catches the whole class, including types nobody has added yet.

_REPO_ROOT = Path(__file__).resolve().parents[3]


# What scripts/setup.sh writes into .env.app after copying the template. The
# template ships these blank ON PURPOSE (CLAUDE.md: templates ship secret keys
# blank, setup.sh generates them on first run), so a faithful guard supplies them
# rather than asserting the raw template loads.
_SETUP_SH_FILLS = {
    "database_url": "postgresql+psycopg2://u:p@localhost:5432/dataq",
    "openbao_token": "dev-root-token",
    "openbao_addr": "http://localhost:8200",
    "openbao_mount": "secret",
}


def test_env_app_template_plus_setup_sh_values_constructs_settings() -> None:
    """The .env.app that setup.sh actually produces must load into Settings.

    This models the documented from-scratch path: copy the template, then fill the
    four keys setup.sh fills. It is deliberately NOT "the raw template loads" —
    that would fail on blanks the template ships by design (OPENBAO_TOKEN), and
    asserting it would encode the opposite of the templates-ship-blank rule.

    Nor is `.env.example` asserted anywhere: it carries compose + frontend vars
    (POSTGRES_*, VITE_*) and Settings is `extra="forbid"`, so it raises by design
    (the #209 split).

    This is the whole-class guard behind #1072 — it catches any unloadable value
    in the shipped template, including on field types nobody has added yet.
    """
    Settings(_env_file=str(_REPO_ROOT / ".env.app.example"), **_SETUP_SH_FILLS)


def test_blank_valued_template_keys_are_parseable_types() -> None:
    """The same guard one layer down, so a failure names the offending key.

    `test_shipped_env_template_constructs_settings` proves the template loads;
    this one says WHICH key broke it and what type it is, because the pydantic
    error alone sent the last investigation to a lineage setting nobody touched.
    """
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
