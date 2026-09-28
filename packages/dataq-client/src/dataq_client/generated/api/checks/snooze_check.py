from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.check_read import CheckRead
from ...models.check_snooze_request import CheckSnoozeRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    check_id: UUID,
    *,
    body: CheckSnoozeRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suites/{suite_id}/checks/{check_id}/snooze".format(
            suite_id=quote(str(suite_id), safe=""),
            check_id=quote(str(check_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CheckRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CheckRead.from_dict(response.json())

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
) -> Response[CheckRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CheckSnoozeRequest,
) -> Response[CheckRead | HTTPValidationError]:
    """Snooze a check's alerts for N hours

    Args:
        suite_id (UUID):
        check_id (UUID):
        body (CheckSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CheckRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CheckSnoozeRequest,
) -> CheckRead | HTTPValidationError | None:
    """Snooze a check's alerts for N hours

    Args:
        suite_id (UUID):
        check_id (UUID):
        body (CheckSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CheckRead | HTTPValidationError
    """

    return sync_detailed(
        suite_id=suite_id,
        check_id=check_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CheckSnoozeRequest,
) -> Response[CheckRead | HTTPValidationError]:
    """Snooze a check's alerts for N hours

    Args:
        suite_id (UUID):
        check_id (UUID):
        body (CheckSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CheckRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CheckSnoozeRequest,
) -> CheckRead | HTTPValidationError | None:
    """Snooze a check's alerts for N hours

    Args:
        suite_id (UUID):
        check_id (UUID):
        body (CheckSnoozeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CheckRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            check_id=check_id,
            client=client,
            body=body,
        )
    ).parsed
