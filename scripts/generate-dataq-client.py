"""Regenerate the dataq-client package's generated layer from the committed OpenAPI spec (#1829).

    python scripts/generate-dataq-client.py          # rewrite packages/dataq-client/.../generated
    python scripts/generate-dataq-client.py --check  # fail if the committed client is stale

The spec is `docs/site/reference/openapi.json`, itself checked against the app in CI, so a route
change must regenerate both. Generation runs into a temporary directory and is compared file by
file, so `--check` never touches the tree.
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess  # nosec B404 — runs the pinned generator, fixed argv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "dataq-client"
TARGET = PACKAGE / "src" / "dataq_client" / "generated"
SPEC = ROOT / "docs" / "site" / "reference" / "openapi.json"


def _generate(into: Path) -> None:
    subprocess.run(  # noqa: S603  # nosec B603 B607 — fixed argv, no shell
        [  # noqa: S607 — the pinned generator on PATH (requirements-dev.txt)
            "openapi-python-client",
            "generate",
            "--path",
            str(SPEC),
            "--meta",
            "none",
            "--output-path",
            str(into),
            "--config",
            str(PACKAGE / "openapi-config.yaml"),
            "--overwrite",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _differences(left: Path, right: Path) -> list[str]:
    compared = filecmp.dircmp(left, right, ignore=["__pycache__", ".ruff_cache"])
    found = [f"only in the fresh client: {n}" for n in compared.left_only]
    found += [f"only in the committed client: {n}" for n in compared.right_only]
    found += [f"differs: {n}" for n in compared.diff_files]
    for name in compared.subdirs:
        found += [f"{name}/{line}" for line in _differences(left / name, right / name)]
    return found


def main() -> int:
    check = "--check" in sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "generated"
        _generate(fresh)
        if check:
            found = _differences(fresh, TARGET) if TARGET.exists() else ["no committed client"]
            if found:
                print("packages/dataq-client is stale — run scripts/generate-dataq-client.py")
                print("\n".join(found[:40]))
                return 1
            return 0
        shutil.rmtree(TARGET, ignore_errors=True)
        shutil.copytree(fresh, TARGET, ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
