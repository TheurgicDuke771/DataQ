"""The one import that wires every LLM feature kind's builder/validator into
`llm_service` (ADR 0042). Both the worker and the API import THIS module, so a
kind cannot be enqueueable in one process and unregistered in the other.
"""

from __future__ import annotations

from backend.app.services import llm_checksuggest, llm_sqlgen
from backend.app.services.llm_service import KIND_BUILDERS

if llm_sqlgen.SQLGEN_KIND not in KIND_BUILDERS:  # pragma: no cover - import-time guard
    raise RuntimeError("llm_sqlgen imported but sql_generation never registered")
if llm_checksuggest.CHECKSUGGEST_KIND not in KIND_BUILDERS:  # pragma: no cover - import-time guard
    raise RuntimeError("llm_checksuggest imported but check_suggestion never registered")

#: Kinds an API surface may enqueue — everything else in LLM_INVOCATION_KINDS is
#: reserved schema vocabulary until its feature module lands here.
REGISTERED_KINDS = frozenset(KIND_BUILDERS)
