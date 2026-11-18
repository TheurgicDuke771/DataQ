from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.asset_metadata_update import AssetMetadataUpdate
from ...models.asset_summary_read import AssetSummaryRead
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    asset_id: UUID,
    *,
    body: AssetMetadataUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/assets/{asset_id}".format(
            asset_id=quote(str(asset_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssetSummaryRead | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AssetSummaryRead.from_dict(response.json())

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
) -> Response[AssetSummaryRead | ErrorResponse]:
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
    body: AssetMetadataUpdate,
) -> Response[AssetSummaryRead | ErrorResponse]:
    """Update asset metadata (workspace-admin only)

    Args:
        asset_id (UUID):
        body (AssetMetadataUpdate): Partial metadata update (workspace-Admin-only). Each field is
            optional; an
            explicit `null` clears it, an omitted field leaves it unchanged — the two are
            distinguished via `model_fields_set` at the route so `owner_user_id: null`
            means "unassign" rather than "leave as is".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetSummaryRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AssetMetadataUpdate,
) -> AssetSummaryRead | ErrorResponse | None:
    """Update asset metadata (workspace-admin only)

    Args:
        asset_id (UUID):
        body (AssetMetadataUpdate): Partial metadata update (workspace-Admin-only). Each field is
            optional; an
            explicit `null` clears it, an omitted field leaves it unchanged — the two are
            distinguished via `model_fields_set` at the route so `owner_user_id: null`
            means "unassign" rather than "leave as is".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetSummaryRead | ErrorResponse
    """

    return sync_detailed(
        asset_id=asset_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AssetMetadataUpdate,
) -> Response[AssetSummaryRead | ErrorResponse]:
    """Update asset metadata (workspace-admin only)

    Args:
        asset_id (UUID):
        body (AssetMetadataUpdate): Partial metadata update (workspace-Admin-only). Each field is
            optional; an
            explicit `null` clears it, an omitted field leaves it unchanged — the two are
            distinguished via `model_fields_set` at the route so `owner_user_id: null`
            means "unassign" rather than "leave as is".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssetSummaryRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: AssetMetadataUpdate,
) -> AssetSummaryRead | ErrorResponse | None:
    """Update asset metadata (workspace-admin only)

    Args:
        asset_id (UUID):
        body (AssetMetadataUpdate): Partial metadata update (workspace-Admin-only). Each field is
            optional; an
            explicit `null` clears it, an omitted field leaves it unchanged — the two are
            distinguished via `model_fields_set` at the route so `owner_user_id: null`
            means "unassign" rather than "leave as is".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssetSummaryRead | ErrorResponse
    """

    return (
        await asyncio_detailed(
            asset_id=asset_id,
            client=client,
            body=body,
        )
    ).parsed
