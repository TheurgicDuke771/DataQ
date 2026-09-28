from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.admin_user_read import AdminUserRead
from ...models.http_validation_error import HTTPValidationError
from ...models.user_role_update import UserRoleUpdate
from ...types import Response


def _get_kwargs(
    user_id: UUID,
    *,
    body: UserRoleUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/admin/users/{user_id}/role".format(
            user_id=quote(str(user_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AdminUserRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AdminUserRead.from_dict(response.json())

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
) -> Response[AdminUserRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    user_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UserRoleUpdate,
) -> Response[AdminUserRead | HTTPValidationError]:
    """Change a user's workspace role (admin)

     Set `user_id`'s stored workspace role — the one sanctioned way to demote.

    Args:
        user_id (UUID):
        body (UserRoleUpdate): `PATCH /admin/users/{id}/role` body (ADR 0033, #742).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminUserRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        user_id=user_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    user_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UserRoleUpdate,
) -> AdminUserRead | HTTPValidationError | None:
    """Change a user's workspace role (admin)

     Set `user_id`'s stored workspace role — the one sanctioned way to demote.

    Args:
        user_id (UUID):
        body (UserRoleUpdate): `PATCH /admin/users/{id}/role` body (ADR 0033, #742).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminUserRead | HTTPValidationError
    """

    return sync_detailed(
        user_id=user_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    user_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UserRoleUpdate,
) -> Response[AdminUserRead | HTTPValidationError]:
    """Change a user's workspace role (admin)

     Set `user_id`'s stored workspace role — the one sanctioned way to demote.

    Args:
        user_id (UUID):
        body (UserRoleUpdate): `PATCH /admin/users/{id}/role` body (ADR 0033, #742).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AdminUserRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        user_id=user_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    user_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UserRoleUpdate,
) -> AdminUserRead | HTTPValidationError | None:
    """Change a user's workspace role (admin)

     Set `user_id`'s stored workspace role — the one sanctioned way to demote.

    Args:
        user_id (UUID):
        body (UserRoleUpdate): `PATCH /admin/users/{id}/role` body (ADR 0033, #742).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AdminUserRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            user_id=user_id,
            client=client,
            body=body,
        )
    ).parsed
