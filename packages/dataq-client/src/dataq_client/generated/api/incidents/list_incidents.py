from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.incident_read import IncidentRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
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

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/incidents",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | list[IncidentRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = IncidentRead.from_dict(response_200_item_data)

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
) -> Response[ErrorResponse | list[IncidentRead]]:
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
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | list[IncidentRead]]:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the incident this cursor names (the
            `X-Next-Cursor` header of the previous page). Costs the same at any depth. Opaque; send it
            instead of `offset`, never with it (422). Incidents sort by `last_seen_at`, which moves:
            an incident bumped past the cursor while you page can be missed, none repeats.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[IncidentRead]]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
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
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | list[IncidentRead] | None:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the incident this cursor names (the
            `X-Next-Cursor` header of the previous page). Costs the same at any depth. Opaque; send it
            instead of `offset`, never with it (422). Incidents sort by `last_seen_at`, which moves:
            an incident bumped past the cursor while you page can be missed, none repeats.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[IncidentRead]
    """

    return sync_detailed(
        client=client,
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
        offset=offset,
        cursor=cursor,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    asset_id: None | Unset | UUID = UNSET,
    suite_id: None | Unset | UUID = UNSET,
    state: None | str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
    cursor: None | str | Unset = UNSET,
) -> Response[ErrorResponse | list[IncidentRead]]:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the incident this cursor names (the
            `X-Next-Cursor` header of the previous page). Costs the same at any depth. Opaque; send it
            instead of `offset`, never with it (422). Incidents sort by `last_seen_at`, which moves:
            an incident bumped past the cursor while you page can be missed, none repeats.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | list[IncidentRead]]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
        offset=offset,
        cursor=cursor,
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
    cursor: None | str | Unset = UNSET,
) -> ErrorResponse | list[IncidentRead] | None:
    """List visible incidents

    Args:
        asset_id (None | Unset | UUID):
        suite_id (None | Unset | UUID):
        state (None | str | Unset): Filter by lifecycle status
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        cursor (None | str | Unset): Start the page after the incident this cursor names (the
            `X-Next-Cursor` header of the previous page). Costs the same at any depth. Opaque; send it
            instead of `offset`, never with it (422). Incidents sort by `last_seen_at`, which moves:
            an incident bumped past the cursor while you page can be missed, none repeats.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | list[IncidentRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            asset_id=asset_id,
            suite_id=suite_id,
            state=state,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
    ).parsed
