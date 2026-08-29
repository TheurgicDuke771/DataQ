"""The one import that wires every LLM feature kind's builder/validator into
`llm_service` (ADR 0042). Both the worker and the API import THIS module, so a
kind cannot be enqueueable in one process and unregistered in the other.
"""

from __future__ import annotations

from backend.app.services import llm_sqlgen  # noqa: F401 — registers sql_generation
from backend.app.services.llm_service import KIND_BUILDERS

#: Kinds an API surface may enqueue — everything else in LLM_INVOCATION_KINDS is
#: reserved schema vocabulary until its feature module lands here.
REGISTERED_KINDS = frozenset(KIND_BUILDERS)
