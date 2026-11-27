"""The frontend names an orchestration provider the way the backend does.

The backend's display name (`OrchestrationProvider.display_name`) is what a run label and an
alert show; the frontend keeps two maps of its own — the trigger-source labels and the
connection-type labels — and a provider spelled differently in any of them reads as two
products. This reads both TypeScript files, so a rename in one place fails here.
"""

import re
from pathlib import Path

import pytest

from backend.app.orchestration.registry import provider_display_names

_API = Path(__file__).resolve().parents[3] / "frontend" / "src" / "api"
_MAPS = [
    ("triggerBindings.ts", "PROVIDER_LABELS"),
    ("connections.ts", "CONNECTION_TYPE_LABELS"),
]


def _entries(filename: str, constant: str) -> dict[str, str]:
    source = (_API / filename).read_text(encoding="utf-8")
    block = re.search(rf"export const {constant}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert block is not None, f"{constant} not found in {filename}"
    entries = dict(re.findall(r"^\s*(\w+):\s*'([^']*)',?$", block.group(1), re.M))
    # A reformatted block (double quotes for a label with an apostrophe) would parse as empty
    # and fail below as a wrong label; say it is the parser.
    assert entries, f"{constant} in {filename} did not parse: one `key: 'label',` per line"
    return entries


@pytest.mark.parametrize(("filename", "constant"), _MAPS)
def test_every_provider_has_the_backends_name(filename: str, constant: str) -> None:
    labels = _entries(filename, constant)
    for provider, name in provider_display_names().items():
        assert labels.get(provider) == name, f"{constant}.{provider} in {filename}"
