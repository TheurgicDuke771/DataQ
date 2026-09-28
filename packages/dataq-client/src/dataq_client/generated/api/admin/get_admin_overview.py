from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.admin_overview_read import AdminOverviewRead
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/admin/overview",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AdminOverviewRead | None:
    if response.status_code == 200:
        response_200 = AdminOverviewRead.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AdminOverviewRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AdminOverviewRead]:
    """Overview stat cards — members, suites, incidents, runs today (admin)

     Counts only. The health signals beside these cards come from
    `GET /admin/health` and `GET /admin/secret-sweep`, which classify their own
    blind spots; nothing here is inferred from them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminOverviewRead]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> AdminOverviewRead | None:
    """Overview stat cards — members, suites, incidents, runs today (admin)

     Counts only. The health signals beside these cards come from
    `GET /admin/health` and `GET /admin/secret-sweep`, which classify their own
    blind spots; nothing here is inferred from them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminOverviewRead
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AdminOverviewRead]:
    """Overview stat cards — members, suites, incidents, runs today (admin)

     Counts only. The health signals beside these cards come from
    `GET /admin/health` and `GET /admin/secret-sweep`, which classify their own
    blind spots; nothing here is inferred from them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminOverviewRead]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> AdminOverviewRead | None:
    """Overview stat cards — members, suites, incidents, runs today (admin)

     Counts only. The health signals beside these cards come from
    `GET /admin/health` and `GET /admin/secret-sweep`, which classify their own
    blind spots; nothing here is inferred from them.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminOverviewRead
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
