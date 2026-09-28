from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.admin_health_read import AdminHealthRead
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/admin/health",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AdminHealthRead | None:
    if response.status_code == 200:
        response_200 = AdminHealthRead.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AdminHealthRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AdminHealthRead]:
    """Workspace health — poll staleness, beat heartbeat, queue depth (admin)

     The signal behind the Overview 'Needs attention' feed and the Integrations
    'Polling health' table (#1696/#1701, epic #1702) — nothing here is derived from a
    per-connection setting DataQ doesn't have: cadence is the fixed workspace-wide
    poll schedule, and every status honestly distinguishes "never observed" from
    "healthy".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminHealthRead]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> AdminHealthRead | None:
    """Workspace health — poll staleness, beat heartbeat, queue depth (admin)

     The signal behind the Overview 'Needs attention' feed and the Integrations
    'Polling health' table (#1696/#1701, epic #1702) — nothing here is derived from a
    per-connection setting DataQ doesn't have: cadence is the fixed workspace-wide
    poll schedule, and every status honestly distinguishes "never observed" from
    "healthy".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminHealthRead
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AdminHealthRead]:
    """Workspace health — poll staleness, beat heartbeat, queue depth (admin)

     The signal behind the Overview 'Needs attention' feed and the Integrations
    'Polling health' table (#1696/#1701, epic #1702) — nothing here is derived from a
    per-connection setting DataQ doesn't have: cadence is the fixed workspace-wide
    poll schedule, and every status honestly distinguishes "never observed" from
    "healthy".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminHealthRead]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> AdminHealthRead | None:
    """Workspace health — poll staleness, beat heartbeat, queue depth (admin)

     The signal behind the Overview 'Needs attention' feed and the Integrations
    'Polling health' table (#1696/#1701, epic #1702) — nothing here is derived from a
    per-connection setting DataQ doesn't have: cadence is the fixed workspace-wide
    poll schedule, and every status honestly distinguishes "never observed" from
    "healthy".

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminHealthRead
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
