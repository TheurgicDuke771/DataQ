from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suite_read import SuiteRead
from ...models.suite_update import SuiteUpdate
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    *,
    body: SuiteUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/suites/{suite_id}".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuiteRead | None:
    if response.status_code == 200:
        response_200 = SuiteRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuiteRead]:
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
    body: SuiteUpdate,
) -> Response[HTTPValidationError | SuiteRead]:
    """Update a suite

    Args:
        suite_id (UUID):
        body (SuiteUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteRead]
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
    body: SuiteUpdate,
) -> HTTPValidationError | SuiteRead | None:
    """Update a suite

    Args:
        suite_id (UUID):
        body (SuiteUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteRead
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
    body: SuiteUpdate,
) -> Response[HTTPValidationError | SuiteRead]:
    """Update a suite

    Args:
        suite_id (UUID):
        body (SuiteUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteRead]
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
    body: SuiteUpdate,
) -> HTTPValidationError | SuiteRead | None:
    """Update a suite

    Args:
        suite_id (UUID):
        body (SuiteUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteRead
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            body=body,
        )
    ).parsed
