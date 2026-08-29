"""Outbound-LLM seam (ADR 0042): provider protocol + the two wire impls.

DataQ *calling* a model — the opposite direction from the MCP server (ADR 0008).
Admin-configured, BYO credential, default-off; output is never trusted more than
user input (callers re-validate through the same gates a human's input rides).
"""

from backend.app.llm.base import (
    LLMNotConfiguredError,
    LLMOutputInvalidError,
    LLMProvider,
    LLMProviderError,
    LLMRequestInvalidError,
    LLMResult,
    LLMUnavailableError,
)

__all__ = [
    "LLMNotConfiguredError",
    "LLMOutputInvalidError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRequestInvalidError",
    "LLMResult",
    "LLMUnavailableError",
]
