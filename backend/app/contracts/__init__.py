"""Data contracts (ADR 0052): ODCS v3 documents in, DataQ suites and checks out, and back.

`odcs` loads and validates a document, `translate` is the fixed ODCS → DataQ mapping (the
ADR's table, one function per row), `export` is its inverse.
"""
