from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.audit_event_read import AuditEventRead
from ...models.audit_event_scrub_request import AuditEventScrubRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    event_id: UUID,
    *,
    body: AuditEventScrubRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/audit-events/{event_id}/scrub".format(
            event_id=quote(str(event_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AuditEventRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AuditEventRead.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AuditEventRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    event_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AuditEventScrubRequest,
) -> Response[AuditEventRead | HTTPValidationError]:
    """Scrub a check audit event's config (workspace-admin only)

     The one exception to append-only (ADR 0041 §9): replace a check event's `config` with
    its tested `column`, for a value that should never have been stored. The row keeps its hash,
    so the chain and anchored heads still verify; an `audit_event.scrub` event records who, why
    and the hashes, never the value. 409 when the event holds nothing to scrub or no longer
    matches its hash.

    Args:
        event_id (UUID):
        body (AuditEventScrubRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditEventRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        event_id=event_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    event_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AuditEventScrubRequest,
) -> AuditEventRead | HTTPValidationError | None:
    """Scrub a check audit event's config (workspace-admin only)

     The one exception to append-only (ADR 0041 §9): replace a check event's `config` with
    its tested `column`, for a value that should never have been stored. The row keeps its hash,
    so the chain and anchored heads still verify; an `audit_event.scrub` event records who, why
    and the hashes, never the value. 409 when the event holds nothing to scrub or no longer
    matches its hash.

    Args:
        event_id (UUID):
        body (AuditEventScrubRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditEventRead | HTTPValidationError
    """

    return sync_detailed(
        event_id=event_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    event_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AuditEventScrubRequest,
) -> Response[AuditEventRead | HTTPValidationError]:
    """Scrub a check audit event's config (workspace-admin only)

     The one exception to append-only (ADR 0041 §9): replace a check event's `config` with
    its tested `column`, for a value that should never have been stored. The row keeps its hash,
    so the chain and anchored heads still verify; an `audit_event.scrub` event records who, why
    and the hashes, never the value. 409 when the event holds nothing to scrub or no longer
    matches its hash.

    Args:
        event_id (UUID):
        body (AuditEventScrubRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditEventRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        event_id=event_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    event_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AuditEventScrubRequest,
) -> AuditEventRead | HTTPValidationError | None:
    """Scrub a check audit event's config (workspace-admin only)

     The one exception to append-only (ADR 0041 §9): replace a check event's `config` with
    its tested `column`, for a value that should never have been stored. The row keeps its hash,
    so the chain and anchored heads still verify; an `audit_event.scrub` event records who, why
    and the hashes, never the value. 409 when the event holds nothing to scrub or no longer
    matches its hash.

    Args:
        event_id (UUID):
        body (AuditEventScrubRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditEventRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            event_id=event_id,
            client=client,
            body=body,
        )
    ).parsed
