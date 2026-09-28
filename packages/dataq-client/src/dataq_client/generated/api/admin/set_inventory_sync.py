from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.inventory_sync_read import InventorySyncRead
from ...models.inventory_sync_update import InventorySyncUpdate
from ...types import Response


def _get_kwargs(
    connection_id: UUID,
    *,
    body: InventorySyncUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/admin/inventory-sync/{connection_id}".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | InventorySyncRead | None:
    if response.status_code == 200:
        response_200 = InventorySyncRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | InventorySyncRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InventorySyncUpdate,
) -> Response[HTTPValidationError | InventorySyncRead]:
    """Turn a connection's inventory sync on or off (admin)

     Flip the ADR 0040 `inventory_sync` toggle (on by default) on the connection's
    config, through the ordinary connection-update path — so the change is audited
    and snapshotted into `connection_versions` exactly like an edit made in the
    connection editor.

    Turning it ON schedules nothing: the sweep is a daily beat task, so use "Run now"
    to see tables before then. Turning it OFF clears the sync bookkeeping and leaves
    already-discovered assets in place.

    Args:
        connection_id (UUID):
        body (InventorySyncUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | InventorySyncRead]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InventorySyncUpdate,
) -> HTTPValidationError | InventorySyncRead | None:
    """Turn a connection's inventory sync on or off (admin)

     Flip the ADR 0040 `inventory_sync` toggle (on by default) on the connection's
    config, through the ordinary connection-update path — so the change is audited
    and snapshotted into `connection_versions` exactly like an edit made in the
    connection editor.

    Turning it ON schedules nothing: the sweep is a daily beat task, so use "Run now"
    to see tables before then. Turning it OFF clears the sync bookkeeping and leaves
    already-discovered assets in place.

    Args:
        connection_id (UUID):
        body (InventorySyncUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | InventorySyncRead
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InventorySyncUpdate,
) -> Response[HTTPValidationError | InventorySyncRead]:
    """Turn a connection's inventory sync on or off (admin)

     Flip the ADR 0040 `inventory_sync` toggle (on by default) on the connection's
    config, through the ordinary connection-update path — so the change is audited
    and snapshotted into `connection_versions` exactly like an edit made in the
    connection editor.

    Turning it ON schedules nothing: the sweep is a daily beat task, so use "Run now"
    to see tables before then. Turning it OFF clears the sync bookkeeping and leaves
    already-discovered assets in place.

    Args:
        connection_id (UUID):
        body (InventorySyncUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | InventorySyncRead]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InventorySyncUpdate,
) -> HTTPValidationError | InventorySyncRead | None:
    """Turn a connection's inventory sync on or off (admin)

     Flip the ADR 0040 `inventory_sync` toggle (on by default) on the connection's
    config, through the ordinary connection-update path — so the change is audited
    and snapshotted into `connection_versions` exactly like an edit made in the
    connection editor.

    Turning it ON schedules nothing: the sweep is a daily beat task, so use "Run now"
    to see tables before then. Turning it OFF clears the sync bookkeeping and leaves
    already-discovered assets in place.

    Args:
        connection_id (UUID):
        body (InventorySyncUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | InventorySyncRead
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
            body=body,
        )
    ).parsed
