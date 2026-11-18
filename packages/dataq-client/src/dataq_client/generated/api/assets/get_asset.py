from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.asset_detail_read import AssetDetailRead
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    asset_id: UUID,
    *,
    score_delta_days: int | Unset = 7,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["score_delta_days"] = score_delta_days

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assets/{asset_id}".format(
            asset_id=quote(str(asset_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssetDetailRead | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AssetDetailRead.from_dict(response.json())

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
) -> Response[AssetDetailRead | ErrorResponse]:
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
    score_delta_days: int | Unset = 7,
) -> Response[AssetDetailRead | ErrorResponse]:
    """Get an asset

    Args:
        asset_id (UUID):
        score_delta_days (int | Unset): How far back `previous_health_score` looks, in days.
            Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetDetailRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        score_delta_days=score_delta_days,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    score_delta_days: int | Unset = 7,
) -> AssetDetailRead | ErrorResponse | None:
    """Get an asset

    Args:
        asset_id (UUID):
        score_delta_days (int | Unset): How far back `previous_health_score` looks, in days.
            Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetDetailRead | ErrorResponse
    """

    return sync_detailed(
        asset_id=asset_id,
        client=client,
        score_delta_days=score_delta_days,
    ).parsed


async def asyncio_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    score_delta_days: int | Unset = 7,
) -> Response[AssetDetailRead | ErrorResponse]:
    """Get an asset

    Args:
        asset_id (UUID):
        score_delta_days (int | Unset): How far back `previous_health_score` looks, in days.
            Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetDetailRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        score_delta_days=score_delta_days,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    score_delta_days: int | Unset = 7,
) -> AssetDetailRead | ErrorResponse | None:
    """Get an asset

    Args:
        asset_id (UUID):
        score_delta_days (int | Unset): How far back `previous_health_score` looks, in days.
            Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetDetailRead | ErrorResponse
    """

    return (
        await asyncio_detailed(
            asset_id=asset_id,
            client=client,
            score_delta_days=score_delta_days,
        )
    ).parsed
