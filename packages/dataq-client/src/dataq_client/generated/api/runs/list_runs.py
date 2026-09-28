from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.run_read import RunRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    suite_id: None | Unset | UUID = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_suite_id: None | str | Unset
    if isinstance(suite_id, Unset):
        json_suite_id = UNSET
    elif isinstance(suite_id, UUID):
        json_suite_id = str(suite_id)
    else:
        json_suite_id = suite_id
    params["suite_id"] = json_suite_id

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    else:
        json_status = status
    params["status"] = json_status

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[RunRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = RunRead.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[HTTPValidationError | list[RunRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | list[RunRead]]:
    """List runs

    Args:
        suite_id (None | Unset | UUID):
        status (None | str | Unset): Filter by run execution status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> HTTPValidationError | list[RunRead] | None:
    """List runs

    Args:
        suite_id (None | Unset | UUID):
        status (None | str | Unset): Filter by run execution status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunRead]
    """

    return sync_detailed(
        client=client,
        suite_id=suite_id,
        status=status,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | list[RunRead]]:
    """List runs

    Args:
        suite_id (None | Unset | UUID):
        status (None | str | Unset): Filter by run execution status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[RunRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> HTTPValidationError | list[RunRead] | None:
    """List runs

    Args:
        suite_id (None | Unset | UUID):
        status (None | str | Unset): Filter by run execution status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[RunRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            suite_id=suite_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    ).parsed
