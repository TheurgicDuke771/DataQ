"""`scripts/check-pnpm-overrides.py` — the override record and the removal sweep (#2509)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-pnpm-overrides.py"
_SPEC = importlib.util.spec_from_file_location("check_pnpm_overrides", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
co = importlib.util.module_from_spec(_SPEC)
sys.modules["check_pnpm_overrides"] = co
_SPEC.loader.exec_module(co)


@pytest.mark.parametrize(
    ("spec", "low"),
    [
        ("^1.1.7", (1, 1, 7)),
        ("~4.3", (4, 3, 0)),
        (">=4.21.0", (4, 21, 0)),
        (">= 4.21.0", (4, 21, 0)),
        (">4.21.0", (4, 21, 1)),
        ("1.2.3 - 2.0.0", (1, 2, 3)),
        ("^3.25.0 || ^4.0.0", (3, 25, 0)),
        (">=1.0.0 <2", (1, 0, 0)),
        ("<2", (0, 0, 0)),
        ("1.x", (1, 0, 0)),
        ("*", (0, 0, 0)),
        ("3.4.15", (3, 4, 15)),
        ("^1.2.3-beta.1", (1, 2, 3)),
    ],
)
def test_min_version(spec: str, low: tuple[int, int, int]) -> None:
    assert co.min_version(spec) == low


@pytest.mark.parametrize("spec", ["npm:other@^1.0.0", "github:owner/repo", "file:../x"])
def test_an_unreadable_range_is_none(spec: str) -> None:
    assert co.min_version(spec) is None


def test_selector() -> None:
    assert co.selector("brace-expansion@5") == ("brace-expansion", 5)
    assert co.selector("postcss") == ("postcss", None)
    assert co.selector("@scope/pkg") == ("@scope/pkg", None)


def _frontend(tmp_path: Path, overrides: dict[str, str], record: dict[str, Any]) -> Path:
    manifest = {"devDependencies": {"vite": "^8.0.0"}, "pnpm": {"overrides": overrides}}
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    (tmp_path / "pnpm-overrides.json").write_text(json.dumps({"_about": "x", **record}))
    return tmp_path


def _install(frontend: Path, name: str, version: str, deps: dict[str, dict[str, str]]) -> None:
    root = frontend / "node_modules" / ".pnpm" / f"{name.replace('/', '+')}@{version}"
    package = root / "node_modules" / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": name, "version": version, **deps}))


_ENTRY = {"why": "floor from a PR", "via": ["vite"]}


def test_the_record_must_match_package_json(tmp_path: Path) -> None:
    frontend = _frontend(
        tmp_path,
        {"postcss": ">=8.5.0 <9", "nanoid": ">=3.3.8 <4"},
        {"postcss": _ENTRY, "zod": _ENTRY, "nanoid": "a string, not an object"},
    )
    assert sorted(co.check_record(frontend)) == sorted(
        [
            "zod: entry in pnpm-overrides.json for no override",
            "nanoid: needs a non-empty string 'why'",
            "nanoid: needs 'via', a non-empty list of package names",
        ]
    )


def test_the_live_record_matches() -> None:
    assert co.check_record() == []


def test_the_sweep_refuses_an_uninstalled_tree(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, {"postcss": ">=8.5.0 <9"}, {"postcss": _ENTRY})
    with pytest.raises(FileNotFoundError):
        co.sweep(frontend)


def test_the_sweep(tmp_path: Path) -> None:
    frontend = _frontend(
        tmp_path,
        {
            "postcss": ">=8.5.0 <9",
            "nanoid": ">=3.3.8 <4",
            "undici": ">=7.29.0 <8",
            "js-yaml": ">=4.1.1 <5",
            "zod@4": ">=4.6.6 <5",
            "pinned": "1.2.3",
        },
        dict.fromkeys(("postcss", "nanoid", "undici", "js-yaml", "zod@4", "pinned"), _ENTRY),
    )
    _install(frontend, "vite", "8.3.8", {"dependencies": {"postcss": "^8.4.0", "nanoid": "^3.3.9"}})
    _install(frontend, "jsdom", "31.0.1", {"dependencies": {"undici": "^8.12.3"}})
    # A peer counts; a dependency's devDependencies are never installed, so they don't.
    _install(
        frontend,
        "@eslint/eslintrc",
        "3.3.5",
        {"peerDependencies": {"js-yaml": ">= 4.0.0"}, "devDependencies": {"nanoid": "^3.0.0"}},
    )
    _install(frontend, "eslint-plugin", "1.0.0", {"dependencies": {"zod": "^3.25.0 || ^4.0.2"}})

    lines, act = co.sweep(frontend)

    assert act is True
    assert lines == [
        "js-yaml: still needed (@eslint/eslintrc@3.3.5 wants >= 4.0.0)",
        "nanoid: no dependent's range goes below >=3.3.8 <4; remove it",
        "pinned: unchecked, '1.2.3' has no >= floor to compare against",
        "postcss: still needed (vite@8.3.8 wants ^8.4.0)",
        "undici: no dependent's range goes below >=7.29.0 <8; remove it; its cap holds back "
        "jsdom@31.0.1 wants ^8.12.3",
        "zod@4: still needed (eslint-plugin@1.0.0 wants ^3.25.0 || ^4.0.2)",
    ]


def test_a_frontend_dev_dependency_counts(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, {"vite": ">=8.3.0 <9"}, {"vite": _ENTRY})
    (frontend / "node_modules" / ".pnpm").mkdir(parents=True)
    lines, _ = co.sweep(frontend)
    assert lines == ["vite: still needed (frontend wants ^8.0.0)"]
