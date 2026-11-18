from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.column_trace_read import ColumnTraceRead
from ...models.error_response import ErrorResponse
from ...models.trace_direction import TraceDirection
from ...types import UNSET, Response, Unset


def _get_kwargs(
    asset_id: UUID,
    *,
    column: str,
    direction: TraceDirection | Unset = UNSET,
    max_depth: int | Unset = 10,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["column"] = column

    json_direction: str | Unset = UNSET
    if not isinstance(direction, Unset):
        json_direction = direction.value

    params["direction"] = json_direction

    params["max_depth"] = max_depth

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assets/{asset_id}/column-lineage".format(
            asset_id=quote(str(asset_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ColumnTraceRead | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ColumnTraceRead.from_dict(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = ErrorResponse.from_dict(response.json())

        return response_401

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ColumnTraceRead | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    column: str,
    direction: TraceDirection | Unset = UNSET,
    max_depth: int | Unset = 10,
) -> Response[ColumnTraceRead | ErrorResponse]:
    """Trace one column's lineage

    Args:
        asset_id (UUID):
        column (str):
        direction (TraceDirection | Unset):
        max_depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ColumnTraceRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        column=column,
        direction=direction,
        max_depth=max_depth,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    column: str,
    direction: TraceDirection | Unset = UNSET,
    max_depth: int | Unset = 10,
) -> ColumnTraceRead | ErrorResponse | None:
    """Trace one column's lineage

    Args:
        asset_id (UUID):
        column (str):
        direction (TraceDirection | Unset):
        max_depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ColumnTraceRead | ErrorResponse
    """

    return sync_detailed(
        asset_id=asset_id,
        client=client,
        column=column,
        direction=direction,
        max_depth=max_depth,
    ).parsed


async def asyncio_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    column: str,
    direction: TraceDirection | Unset = UNSET,
    max_depth: int | Unset = 10,
) -> Response[ColumnTraceRead | ErrorResponse]:
    """Trace one column's lineage

    Args:
        asset_id (UUID):
        column (str):
        direction (TraceDirection | Unset):
        max_depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ColumnTraceRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        column=column,
        direction=direction,
        max_depth=max_depth,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    column: str,
    direction: TraceDirection | Unset = UNSET,
    max_depth: int | Unset = 10,
) -> ColumnTraceRead | ErrorResponse | None:
    """Trace one column's lineage

    Args:
        asset_id (UUID):
        column (str):
        direction (TraceDirection | Unset):
        max_depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ColumnTraceRead | ErrorResponse
    """

    return (
        await asyncio_detailed(
            asset_id=asset_id,
            client=client,
            column=column,
            direction=direction,
            max_depth=max_depth,
        )
    ).parsed
