from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.membership_read import MembershipRead
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/admin/members",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> MembershipRead | None:
    if response.status_code == 200:
        response_200 = MembershipRead.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[MembershipRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[MembershipRead]:
    """Workspace members (admin)

     Who is admitted to this workspace, and whether membership is being enforced.

    An empty list means enforcement is OFF, not that nobody has access: who may
    sign in is then decided entirely by the deployment's env allowlists.
    Addresses those allowlists name appear as read-only `env` rows, so a removal
    that an env var would undo never looks complete.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[MembershipRead]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> MembershipRead | None:
    """Workspace members (admin)

     Who is admitted to this workspace, and whether membership is being enforced.

    An empty list means enforcement is OFF, not that nobody has access: who may
    sign in is then decided entirely by the deployment's env allowlists.
    Addresses those allowlists name appear as read-only `env` rows, so a removal
    that an env var would undo never looks complete.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        MembershipRead
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[MembershipRead]:
    """Workspace members (admin)

     Who is admitted to this workspace, and whether membership is being enforced.

    An empty list means enforcement is OFF, not that nobody has access: who may
    sign in is then decided entirely by the deployment's env allowlists.
    Addresses those allowlists name appear as read-only `env` rows, so a removal
    that an env var would undo never looks complete.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[MembershipRead]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> MembershipRead | None:
    """Workspace members (admin)

     Who is admitted to this workspace, and whether membership is being enforced.

    An empty list means enforcement is OFF, not that nobody has access: who may
    sign in is then decided entirely by the deployment's env allowlists.
    Addresses those allowlists name appear as read-only `env` rows, so a removal
    that an env var would undo never looks complete.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        MembershipRead
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
