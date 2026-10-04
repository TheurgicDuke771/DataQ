from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.bulk_checks_result import BulkChecksResult
from ...models.bulk_snooze_request import BulkSnoozeRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    *,
    body: BulkSnoozeRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suites/{suite_id}/checks-bulk/snooze".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BulkChecksResult | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = BulkChecksResult.from_dict(response.json())

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
) -> Response[BulkChecksResult | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: BulkSnoozeRequest,
) -> Response[BulkChecksResult | HTTPValidationError]:
    """Snooze the alerts of many checks

     All-or-nothing: if any id is not a check of this suite the request is refused with 404 and nothing
    is changed. Duplicate ids count once.

    Args:
        suite_id (UUID):
        body (BulkSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BulkChecksResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: BulkSnoozeRequest,
) -> BulkChecksResult | HTTPValidationError | None:
    """Snooze the alerts of many checks

     All-or-nothing: if any id is not a check of this suite the request is refused with 404 and nothing
    is changed. Duplicate ids count once.

    Args:
        suite_id (UUID):
        body (BulkSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BulkChecksResult | HTTPValidationError
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: BulkSnoozeRequest,
) -> Response[BulkChecksResult | HTTPValidationError]:
    """Snooze the alerts of many checks

     All-or-nothing: if any id is not a check of this suite the request is refused with 404 and nothing
    is changed. Duplicate ids count once.

    Args:
        suite_id (UUID):
        body (BulkSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BulkChecksResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: BulkSnoozeRequest,
) -> BulkChecksResult | HTTPValidationError | None:
    """Snooze the alerts of many checks

     All-or-nothing: if any id is not a check of this suite the request is refused with 404 and nothing
    is changed. Duplicate ids count once.

    Args:
        suite_id (UUID):
        body (BulkSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BulkChecksResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            body=body,
        )
    ).parsed
