from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.pipeline_run_read import PipelineRunRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
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

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/pipeline_runs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[PipelineRunRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = PipelineRunRead.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[PipelineRunRead]]:
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
) -> Response[HTTPValidationError | list[PipelineRunRead]]:
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

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
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
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> HTTPValidationError | list[PipelineRunRead] | None:
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

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[PipelineRunRead]
    """

    return sync_detailed(
        client=client,
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | list[PipelineRunRead]]:
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

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
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
) -> HTTPValidationError | list[PipelineRunRead] | None:
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

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[PipelineRunRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            provider=provider,
            status=status,
            limit=limit,
            offset=offset,
        )
    ).parsed
