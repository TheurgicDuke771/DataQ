from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connection_version_read import ConnectionVersionRead
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    connection_id: UUID,
    version_no: int,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/v1/connections/{connection_id}/versions/{version_no}".format(
            connection_id=quote(str(connection_id), safe=""),
            version_no=quote(str(version_no), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectionVersionRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ConnectionVersionRead.from_dict(response.json())

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
) -> Response[ConnectionVersionRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    connection_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ConnectionVersionRead | HTTPValidationError]:
    """Redact one version's config (empty it, keep the row)

     For a value pasted into a config field by mistake and since edited out: the snapshot's
    `config` is emptied and the row stamped with who redacted it and when (an already-redacted
    version is returned unchanged); the row and the rest
    of the history stay. The newest version is refused (409) — it is the connection's current
    state. Admin-only like every connection mutation; audited with the version number only.

    Args:
        connection_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionVersionRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        version_no=version_no,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> ConnectionVersionRead | HTTPValidationError | None:
    """Redact one version's config (empty it, keep the row)

     For a value pasted into a config field by mistake and since edited out: the snapshot's
    `config` is emptied and the row stamped with who redacted it and when (an already-redacted
    version is returned unchanged); the row and the rest
    of the history stay. The newest version is refused (409) — it is the connection's current
    state. Admin-only like every connection mutation; audited with the version number only.

    Args:
        connection_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionVersionRead | HTTPValidationError
    """

    return sync_detailed(
        connection_id=connection_id,
        version_no=version_no,
        client=client,
    ).parsed


async def asyncio_detailed(
    connection_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ConnectionVersionRead | HTTPValidationError]:
    """Redact one version's config (empty it, keep the row)

     For a value pasted into a config field by mistake and since edited out: the snapshot's
    `config` is emptied and the row stamped with who redacted it and when (an already-redacted
    version is returned unchanged); the row and the rest
    of the history stay. The newest version is refused (409) — it is the connection's current
    state. Admin-only like every connection mutation; audited with the version number only.

    Args:
        connection_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionVersionRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        version_no=version_no,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> ConnectionVersionRead | HTTPValidationError | None:
    """Redact one version's config (empty it, keep the row)

     For a value pasted into a config field by mistake and since edited out: the snapshot's
    `config` is emptied and the row stamped with who redacted it and when (an already-redacted
    version is returned unchanged); the row and the rest
    of the history stay. The newest version is refused (409) — it is the connection's current
    state. Admin-only like every connection mutation; audited with the version number only.

    Args:
        connection_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionVersionRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            version_no=version_no,
            client=client,
        )
    ).parsed
