"""Fail if anything dataq-client installs at runtime is strong-copyleft or source-available.

DataQ ships MIT (ADR 0031); the client is a separate distribution users install into their own
environments, so its runtime tree gets the same check. Walks the installed dependency closure.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import sys

FORBIDDEN = re.compile(r"\b(A?GPL|SSPL|BUSL|Business Source|Elastic|Commons Clause)\b", re.I)
# LGPL is weak copyleft and allowed with notices; it must not match the GPL rule.
WEAK = re.compile(r"\bLGPL\b|Lesser General Public", re.I)


def _licence(dist: metadata.PackageMetadata) -> str:
    expression = dist.get("License-Expression")
    classifiers = [c for c in dist.get_all("Classifier") or [] if c.startswith("License ::")]
    return expression or "; ".join(classifiers) or (dist.get("License") or "")[:120]


def _closure(root: str) -> dict[str, str]:
    seen: dict[str, str] = {}
    pending = [root]
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = _licence(dist)
        for requirement in metadata.requires(name) or []:
            if "extra ==" in requirement:
                continue
            pending.append(re.split(r"[\s;<>=!~\[]", requirement, maxsplit=1)[0])
    return seen


def main() -> int:
    tree = _closure("dataq-client")
    bad = {n: lic for n, lic in tree.items() if FORBIDDEN.search(lic) and not WEAK.search(lic)}
    unknown = [n for n, lic in tree.items() if not lic.strip()]
    for name, lic in sorted(tree.items()):
        print(f"{name}: {lic or '(none declared)'}")
    if bad or unknown:
        print(f"forbidden: {bad}  undeclared: {unknown}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
