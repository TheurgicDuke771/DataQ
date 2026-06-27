"""Result-publishing seam (ADR 0011).

A completed run's outcome is dispatched through a small ``ResultPublisher``
interface rather than a hardcoded MS Teams call, so post-v1 publishers (JIRA /
TestRail / Xray) become additional subscribers with no re-plumbing. The v1
implementation is the Teams notifier; this package owns the seam, the
boundary-crossing report DTO, the PII-redaction policy applied at the seam, and
the no-op used when nothing is configured.
"""
