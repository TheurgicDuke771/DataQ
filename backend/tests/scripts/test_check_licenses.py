"""`scripts/check-licenses.py` — the rule-40 allowlist, overrides and notices (ADR 0031)."""

from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-licenses.py"
_SPEC = importlib.util.spec_from_file_location("check_licenses", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
cl = importlib.util.module_from_spec(_SPEC)
sys.modules["check_licenses"] = cl  # dataclasses resolve the module's annotations
_SPEC.loader.exec_module(cl)


@pytest.mark.parametrize(
    "expression",
    [
        "MIT",
        "MIT License",
        "Apache Software License",
        "Apache-2.0 AND MIT",
        "Apache Software License; BSD License",
        "(MPL-2.0 OR Apache-2.0)",
        "MIT OR GPL-3.0-only",
        "Apache-2.0 WITH LLVM-exception",
        "GNU Library or Lesser General Public License (LGPL)",
        "LGPL-3.0-or-later",
        "Python Software Foundation License",
        "The Unlicense (Unlicense)",
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        "OFL-1.1",
        "Historical Permission Notice and Disclaimer (HPND)",
        "GNU Lesser General Public License v2 (LGPLv2)",
        "MIT-CMU",
    ],
)
def test_allowed_expressions(expression: str) -> None:
    assert cl.allowed(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "UNKNOWN",
        "",
        "GPL-3.0-only",
        "GNU General Public License v3 (GPLv3)",
        "AGPL-3.0",
        "SSPL-1.0",
        "BUSL-1.1",
        "Elastic-2.0",
        "MIT AND GPL-2.0-only",
        "Artistic License; GNU General Public License (GPL)",
        "Something-Nobody-Reviewed-1.0",
        "Proprietary",
        "MIT (see LICENSE)",
        "MIT ) AND GPL-3.0-only",
        "Apache-2.0 (GPL-3.0 for the bundled parts)",
    ],
)
def test_rejected_expressions(expression: str) -> None:
    assert not cl.allowed(expression)


def test_forbidden_pattern_spares_lgpl() -> None:
    assert cl.FORBIDDEN.search("GPL-3.0-only")
    assert cl.FORBIDDEN.search("GNU Affero General Public License v3")
    assert not cl.FORBIDDEN.search("LGPL-3.0-only")
    assert not cl.FORBIDDEN.search("GNU Library or Lesser General Public License (LGPL)")


def _pkg(name: str, licence: str, version: str = "1.0") -> Any:
    return cl.Package(name=name, version=version, licence=licence)


def test_check_fails_on_forbidden_and_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    packages = [_pkg("fine", "MIT"), _pkg("copyleft", "GPL-3.0-only"), _pkg("mystery", "UNKNOWN")]
    assert cl.check("backend", packages, {}) == 1
    out = capsys.readouterr().out
    assert "copyleft 1.0: 'GPL-3.0-only' — forbidden" in out
    assert "mystery 1.0: 'UNKNOWN' — forbidden" in out
    assert "fine" not in out.split("violation(s)")[1]


def test_check_passes_clean_set() -> None:
    assert cl.check("backend", [_pkg("a", "MIT"), _pkg("b", "BSD License")], {}) == 0


def _overrides(tmp_path: Path, body: str) -> Any:
    path = tmp_path / "overrides.toml"
    path.write_text(body, encoding="utf-8")
    return cl.load_overrides(path)


def test_override_rescues_unknown_metadata_with_evidence(tmp_path: Path) -> None:
    overrides = _overrides(
        tmp_path,
        '[backend.My_Pkg]\nlicense = "Apache-2.0"\nevidence = "https://example.test/COPYING"\n'
        'reason = "wheel has no licence metadata"\n',
    )
    pkg = _pkg("my-pkg", "UNKNOWN")
    assert cl.check("backend", [pkg], overrides) == 0
    assert pkg.licence == "Apache-2.0"
    assert "declared 'UNKNOWN'" in pkg.override and "https://example.test/COPYING" in pkg.override


def test_override_is_side_and_version_scoped(tmp_path: Path) -> None:
    overrides = _overrides(
        tmp_path,
        '[frontend.pkg]\nlicense = "MIT"\nevidence = "https://example.test"\nreason = "r"\n'
        'version = "2.0"\n'
        '[frontend.other]\nlicense = "MIT"\nevidence = "https://example.test"\nreason = "r"\n',
    )
    assert cl.check("backend", [_pkg("other", "UNKNOWN")], overrides) == 1
    assert cl.check("frontend", [_pkg("other", "UNKNOWN")], overrides) == 0
    assert cl.check("frontend", [_pkg("pkg", "UNKNOWN", version="1.0")], overrides) == 1
    assert cl.check("frontend", [_pkg("pkg", "UNKNOWN", version="2.0")], overrides) == 0
    # pnpm reports a package installed in several versions as one row.
    assert cl.check("frontend", [_pkg("pkg", "UNKNOWN", version="1.0, 2.0")], overrides) == 0


def test_override_is_inert_once_the_metadata_is_fixed(tmp_path: Path) -> None:
    overrides = _overrides(
        tmp_path,
        '[backend.pkg]\nlicense = "Apache-2.0"\nevidence = "https://example.test"\nreason = "r"\n',
    )
    pkg = _pkg("pkg", "BSD-3-Clause")
    assert cl.check("backend", [pkg], overrides) == 0
    assert pkg.licence == "BSD-3-Clause" and pkg.override == ""


def test_override_cannot_admit_a_forbidden_licence(tmp_path: Path) -> None:
    overrides = _overrides(
        tmp_path,
        '[backend.pkg]\nlicense = "GPL-3.0-only"\nevidence = "https://example.test"\n'
        'reason = "r"\n',
    )
    assert cl.check("backend", [_pkg("pkg", "UNKNOWN")], overrides) == 1


def test_override_without_evidence_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="needs `evidence`"):
        _overrides(tmp_path, '[backend.pkg]\nlicense = "MIT"\nreason = "r"\n')


def test_committed_overrides_file_is_well_formed() -> None:
    overrides = cl.load_overrides()
    for key, entry in overrides.items():
        assert cl.allowed(entry["license"]), key
        assert entry["evidence"].startswith("https://"), key


def test_backend_closure_follows_extras_and_requirement_includes(tmp_path: Path) -> None:
    child = tmp_path / "child.txt"
    child.write_text(f"pytest=={metadata.version('pytest')}  # comment\n", encoding="utf-8")
    parent = tmp_path / "parent.txt"
    parent.write_text(
        "-r child.txt\n\n# only a comment\ncelery[redis]\n"
        "dataq-no-such-package-xyz; sys_platform == 'nonesuch'\n",
        encoding="utf-8",
    )
    names = {p.name.lower() for p in cl.backend_closure(cl.read_requirements(parent))}
    assert {"pytest", "pluggy", "celery", "redis"} <= names


def test_backend_closure_refuses_a_pin_the_env_has_not_installed(tmp_path: Path) -> None:
    req = tmp_path / "r.txt"
    req.write_text("pytest==0.0.1\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"pinned ==0\.0\.1, installed"):
        cl.backend_closure(cl.read_requirements(req))


def test_backend_closure_refuses_a_missing_distribution(tmp_path: Path) -> None:
    req = tmp_path / "r.txt"
    req.write_text("dataq-no-such-package-xyz\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not installed"):
        cl.backend_closure(cl.read_requirements(req))


def test_notices_carry_texts_and_overrides() -> None:
    pkg = cl.Package(
        name="demo",
        version="1.2",
        licence="MIT",
        homepage="https://example.test/demo",
        texts=[
            ("LICENSE", "Copyright (c) Demo\nPermission is hereby granted"),
            ("NOTICE", "Demo NOTICE"),
        ],
        override="declared 'UNKNOWN'; reviewed — https://example.test/LICENSE",
    )
    bare = cl.Package(name="bare", version="0.1", licence="BSD-3-Clause")
    text = cl.render_notices("DataQ backend", [pkg, bare])
    assert "| demo | 1.2 | MIT (reviewed override: see below) | https://example.test/demo |" in text
    assert "Permission is hereby granted" in text and "Demo NOTICE" in text
    assert "Licence override: declared 'UNKNOWN'" in text
    assert "No licence text is shipped in the distribution" in text


def test_frontend_packages_parse_pnpm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "MIT": [{"name": "b", "versions": ["1.0"], "license": "MIT", "homepage": "https://b"}],
        "Unknown": [{"name": "a", "versions": ["2.0", "2.1"], "license": "Unknown"}],
    }

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert cmd[:4] == ["pnpm", "licenses", "list", "--json"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(cl.subprocess, "run", fake_run)
    packages = cl.frontend_packages(prod=False)
    assert [(p.name, p.version, p.licence) for p in packages] == [
        ("a", "2.0, 2.1", "Unknown"),
        ("b", "1.0", "MIT"),
    ]
    assert cl.check("frontend", packages, {}) == 1


def test_main_fails_on_a_forbidden_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = tmp_path / "r.txt"
    req.write_text("pytest\n", encoding="utf-8")
    original = cl.backend_closure

    def poisoned(requirements: list[object]) -> list[object]:
        return [*original(requirements), _pkg("evil-sspl", "SSPL-1.0")]

    monkeypatch.setattr(cl, "backend_closure", poisoned)
    notices = tmp_path / "out.md"
    assert cl.main(["x", "backend", "--requirements", str(req), "--notices", str(notices)]) == 1
    assert not notices.exists()
    monkeypatch.setattr(cl, "backend_closure", original)
    assert cl.main(["x", "backend", "--requirements", str(req), "--notices", str(notices)]) == 0
    assert notices.read_text(encoding="utf-8").startswith("# Third-party notices — DataQ backend")


def test_table_cells_escape_backslash_before_pipe_and_collapse_whitespace() -> None:
    assert cl._cell("a|b\\c\nd") == "a\\|b\\\\c d"
    pkg = cl.Package(name="x", version="1", licence="MIT", homepage="https://h|p", author="A\\B")
    table_line = next(
        line for line in cl.render_notices("t", [pkg]).splitlines() if line.startswith("| x ")
    )
    assert table_line == "| x | 1 | MIT | https://h\\|p |"
