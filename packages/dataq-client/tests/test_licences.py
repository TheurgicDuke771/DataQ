"""The runtime licence check catches every spelling of a forbidden licence (#1829)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_licences", Path(__file__).parents[1] / "scripts" / "check_licences.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_licences = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_licences)


@pytest.mark.parametrize(
    "licence",
    [
        "GPL-3.0-only",
        "AGPL-3.0-or-later",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "SSPL-1.0",
        "BUSL-1.1",
        "Elastic License 2.0",
    ],
)
def test_forbidden_licences_are_caught(licence: str) -> None:
    assert check_licences.FORBIDDEN.search(licence)


@pytest.mark.parametrize(
    "licence",
    [
        "MIT",
        "BSD-3-Clause",
        "Apache-2.0",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
        "LGPL-2.1-or-later",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
    ],
)
def test_permissive_and_weak_copyleft_licences_pass(licence: str) -> None:
    assert not check_licences.FORBIDDEN.search(licence)
