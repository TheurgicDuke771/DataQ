from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pipeline_run_read import PipelineRunRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_provider: None | str | Unset
    if isinstance(provider, Unset):
        json_provider = UNSET
    else:
        json_provider = provider
    params["provider"] = json_provider

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    else:
        json_status = status
    params["status"] = json_status

    params["limit"] = limit

    params["offset"] = offset

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/pipeline_runs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | list[PipelineRunRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = PipelineRunRead.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[ErrorResponse | list[PipelineRunRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | list[PipelineRunRead]]:
    """List monitored orchestrator pipeline/DAG runs

     Paged (#928): before this, `limit` capped at 200 with no `offset`, so the 200 most recent
    runs were the only ones any client could ever see — and a caller passing `?offset=` got it
    silently discarded by FastAPI and re-read page 1 forever. A paging loop therefore "counted"
    20,200 rows against a 211-row table.

    Args:
        provider (None | str | Unset):
        status (None | str | Unset): Filter by pipeline-run status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the row this cursor names (the `X-Next-
            Cursor` header of the previous page of the same list). Costs the same at any depth, where
            a large `offset` costs in proportion to it. Opaque; send it instead of `offset`, never
            with it (422). Take `X-Total-Count` from the first page: a cursor page does not carry it.
            A row that commits after the cursor has passed its timestamp can be missed.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
        cursor=cursor,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | list[PipelineRunRead] | None:
    """List monitored orchestrator pipeline/DAG runs

     Paged (#928): before this, `limit` capped at 200 with no `offset`, so the 200 most recent
    runs were the only ones any client could ever see — and a caller passing `?offset=` got it
    silently discarded by FastAPI and re-read page 1 forever. A paging loop therefore "counted"
    20,200 rows against a 211-row table.

    Args:
        provider (None | str | Unset):
        status (None | str | Unset): Filter by pipeline-run status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the row this cursor names (the `X-Next-
            Cursor` header of the previous page of the same list). Costs the same at any depth, where
            a large `offset` costs in proportion to it. Opaque; send it instead of `offset`, never
            with it (422). Take `X-Total-Count` from the first page: a cursor page does not carry it.
            A row that commits after the cursor has passed its timestamp can be missed.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[PipelineRunRead]
    """

    return sync_detailed(
        client=client,
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | list[PipelineRunRead]]:
    """List monitored orchestrator pipeline/DAG runs

     Paged (#928): before this, `limit` capped at 200 with no `offset`, so the 200 most recent
    runs were the only ones any client could ever see — and a caller passing `?offset=` got it
    silently discarded by FastAPI and re-read page 1 forever. A paging loop therefore "counted"
    20,200 rows against a 211-row table.

    Args:
        provider (None | str | Unset):
        status (None | str | Unset): Filter by pipeline-run status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the row this cursor names (the `X-Next-
            Cursor` header of the previous page of the same list). Costs the same at any depth, where
            a large `offset` costs in proportion to it. Opaque; send it instead of `offset`, never
            with it (422). Take `X-Total-Count` from the first page: a cursor page does not carry it.
            A row that commits after the cursor has passed its timestamp can be missed.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
        cursor=cursor,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | list[PipelineRunRead] | None:
    """List monitored orchestrator pipeline/DAG runs

     Paged (#928): before this, `limit` capped at 200 with no `offset`, so the 200 most recent
    runs were the only ones any client could ever see — and a caller passing `?offset=` got it
    silently discarded by FastAPI and re-read page 1 forever. A paging loop therefore "counted"
    20,200 rows against a 211-row table.

    Args:
        provider (None | str | Unset):
        status (None | str | Unset): Filter by pipeline-run status. Closed vocabulary — a value
            outside ['queued', 'running', 'succeeded', 'failed', 'cancelled'] is a 422, never a silent
            empty page.
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the row this cursor names (the `X-Next-
            Cursor` header of the previous page of the same list). Costs the same at any depth, where
            a large `offset` costs in proportion to it. Opaque; send it instead of `offset`, never
            with it (422). Take `X-Total-Count` from the first page: a cursor page does not carry it.
            A row that commits after the cursor has passed its timestamp can be missed.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[PipelineRunRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            provider=provider,
            status=status,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
    ).parsed
