from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.incident_read import IncidentRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_asset_id: None | str | Unset
    if isinstance(asset_id, Unset):
        json_asset_id = UNSET
    elif isinstance(asset_id, UUID):
        json_asset_id = str(asset_id)
    else:
        json_asset_id = asset_id
    params["asset_id"] = json_asset_id

    json_suite_id: None | str | Unset
    if isinstance(suite_id, Unset):
        json_suite_id = UNSET
    elif isinstance(suite_id, UUID):
        json_suite_id = str(suite_id)
    else:
        json_suite_id = suite_id
    params["suite_id"] = json_suite_id

    json_state: None | str | Unset
    if isinstance(state, Unset):
        json_state = UNSET
    else:
        json_state = state
    params["state"] = json_state

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/incidents",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[IncidentRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = IncidentRead.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[IncidentRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | list[IncidentRead]]:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[IncidentRead]]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
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
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> HTTPValidationError | list[IncidentRead] | None:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[IncidentRead]
    """

    return sync_detailed(
        client=client,
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | list[IncidentRead]]:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[IncidentRead]]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> HTTPValidationError | list[IncidentRead] | None:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[IncidentRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            asset_id=asset_id,
            suite_id=suite_id,
            state=state,
            limit=limit,
            offset=offset,
        )
    ).parsed
