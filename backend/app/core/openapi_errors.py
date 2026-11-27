"""Type every error response in the OpenAPI document as DataQ's error envelope.

Every 4xx and 5xx the API returns is ``{"error": {"code", "message", "detail"}}``
(`core.errors`), but FastAPI documents a request-validation failure as its own
``HTTPValidationError`` and no other error at all. A client generated from that document
parsed a 422 into an empty object and a 404, 403 or 409 into nothing, so the server's message
was lost. The document is rewritten once, when it is first built:

- an operation's ``422`` is the envelope, not ``HTTPValidationError``;
- every operation declares the other statuses the API can answer with, each the envelope.

The extra statuses are the ones any operation can return (authentication, the workspace role,
a missing or conflicting resource, rate limiting), not a claim about each handler: a client that
receives one parses the envelope and keeps the server's message. A status outside the list is
still an unexpected status to the generated client, as before.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from backend.app.core.errors import ErrorResponse

_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
_REF = "#/components/schemas/ErrorResponse"
_CONTENT = {"application/json": {"schema": {"$ref": _REF}}}
_ERRORS = {
    "401": "Not signed in, or the credential is not valid (`unauthenticated`).",
    "403": "The caller's role or access to this resource does not allow it (`forbidden`).",
    "404": "The resource does not exist, or the caller cannot see it (`not_found`).",
    "409": "The request conflicts with the resource's current state.",
    "422": "The request is not valid (`validation_error`).",
    "429": "Too many requests: retry after the `Retry-After` header (`rate_limited`).",
}
_FASTAPI_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _envelope_schemas() -> dict[str, Any]:
    schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    defs = schema.pop("$defs", {})
    return {"ErrorResponse": schema, **defs}


def apply_error_envelope(spec: dict[str, Any]) -> None:
    """Rewrite ``spec`` in place."""
    for operations in spec.get("paths", {}).values():
        for method, operation in operations.items():
            if method not in _METHODS:
                continue
            responses = operation.setdefault("responses", {})
            for status, description in _ERRORS.items():
                # A route that documents its own error keeps its description; only the body is
                # the envelope.
                declared = responses.get(status, {})
                responses[status] = {
                    "description": declared.get("description", description),
                    "content": _CONTENT,
                }
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    envelope = _envelope_schemas()
    clash = [n for n in envelope if n in schemas]
    if clash:
        raise RuntimeError(f"the OpenAPI document already has a schema named {clash}")
    schemas.update(envelope)
    # FastAPI's own validation-error schemas are unused once every 422 is the envelope.
    for name in _FASTAPI_SCHEMAS:
        schemas.pop(name, None)


def install(app: FastAPI) -> None:
    """Make ``app.openapi()`` return the rewritten document."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            apply_error_envelope(build())
        assert app.openapi_schema is not None  # nosec B101 — set by `build`
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
