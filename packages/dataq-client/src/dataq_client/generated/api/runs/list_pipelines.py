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
    env: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_provider: None | str | Unset
    if isinstance(provider, Unset):
        json_provider = UNSET
    else:
        json_provider = provider
    params["provider"] = json_provider

    json_env: None | str | Unset
    if isinstance(env, Unset):
        json_env = UNSET
    else:
        json_env = env
    params["env"] = json_env

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/orchestration/pipelines",
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
    env: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | list[PipelineRunRead]]:
    """List monitored pipelines with their latest run status

     The pipeline status view: one row per monitored pipeline (provider /
    pipeline-or-dag / env), carrying its most-recent run, most-recently-active
    first. Auth-only gated (orchestration monitoring, not suite-scoped); the
    per-run feed is `/pipeline_runs`.

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        env=env,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | list[PipelineRunRead] | None:
    """List monitored pipelines with their latest run status

     The pipeline status view: one row per monitored pipeline (provider /
    pipeline-or-dag / env), carrying its most-recent run, most-recently-active
    first. Auth-only gated (orchestration monitoring, not suite-scoped); the
    per-run feed is `/pipeline_runs`.

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[PipelineRunRead]
    """

    return sync_detailed(
        client=client,
        provider=provider,
        env=env,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | list[PipelineRunRead]]:
    """List monitored pipelines with their latest run status

     The pipeline status view: one row per monitored pipeline (provider /
    pipeline-or-dag / env), carrying its most-recent run, most-recently-active
    first. Auth-only gated (orchestration monitoring, not suite-scoped); the
    per-run feed is `/pipeline_runs`.

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[PipelineRunRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        env=env,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | list[PipelineRunRead] | None:
    """List monitored pipelines with their latest run status

     The pipeline status view: one row per monitored pipeline (provider /
    pipeline-or-dag / env), carrying its most-recent run, most-recently-active
    first. Auth-only gated (orchestration monitoring, not suite-scoped); the
    per-run feed is `/pipeline_runs`.

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        limit (int | Unset):  Default: 50.

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
            env=env,
            limit=limit,
        )
    ).parsed
