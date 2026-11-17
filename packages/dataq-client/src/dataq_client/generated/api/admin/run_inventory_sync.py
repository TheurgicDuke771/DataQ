from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.inventory_sync_run_response import InventorySyncRunResponse
from ...types import Response


def _get_kwargs(
    connection_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/inventory-sync/{connection_id}/run".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | InventorySyncRunResponse | None:
    if response.status_code == 202:
        response_202 = InventorySyncRunResponse.from_dict(response.json())

        return response_202

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
) -> Response[ErrorResponse | InventorySyncRunResponse]:
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
) -> Response[ErrorResponse | InventorySyncRunResponse]:
    """Run one connection's inventory sync now (admin)

     Enqueue the existing sync for this connection. It runs whether or not the
    connection is opted in — the opt-in gates the unattended nightly sweep, and an
    admin asking for this connection has asked for it explicitly. 503 with a classified
    broker reason if nothing could be enqueued; no audit row in that case.

    Args:
        connection_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InventorySyncRunResponse]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | InventorySyncRunResponse | None:
    """Run one connection's inventory sync now (admin)

     Enqueue the existing sync for this connection. It runs whether or not the
    connection is opted in — the opt-in gates the unattended nightly sweep, and an
    admin asking for this connection has asked for it explicitly. 503 with a classified
    broker reason if nothing could be enqueued; no audit row in that case.

    Args:
        connection_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InventorySyncRunResponse
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | InventorySyncRunResponse]:
    """Run one connection's inventory sync now (admin)

     Enqueue the existing sync for this connection. It runs whether or not the
    connection is opted in — the opt-in gates the unattended nightly sweep, and an
    admin asking for this connection has asked for it explicitly. 503 with a classified
    broker reason if nothing could be enqueued; no audit row in that case.

    Args:
        connection_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | InventorySyncRunResponse]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | InventorySyncRunResponse | None:
    """Run one connection's inventory sync now (admin)

     Enqueue the existing sync for this connection. It runs whether or not the
    connection is opted in — the opt-in gates the unattended nightly sweep, and an
    admin asking for this connection has asked for it explicitly. 503 with a classified
    broker reason if nothing could be enqueued; no audit row in that case.

    Args:
        connection_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | InventorySyncRunResponse
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
        )
    ).parsed
