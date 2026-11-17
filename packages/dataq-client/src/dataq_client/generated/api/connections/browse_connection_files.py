from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.file_browse_read import FileBrowseRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    connection_id: UUID,
    *,
    prefix: str | Unset = "",
    limit: int | Unset = 200,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["prefix"] = prefix

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/connections/{connection_id}/browse/files".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FileBrowseRead | None:
    if response.status_code == 200:
        response_200 = FileBrowseRead.from_dict(response.json())

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
) -> Response[ErrorResponse | FileBrowseRead]:
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
    prefix: str | Unset = "",
    limit: int | Unset = 200,
) -> Response[ErrorResponse | FileBrowseRead]:
    """List the folders and files under a prefix of an ADLS Gen2 / S3 / GCS connection

    Args:
        connection_id (UUID):
        prefix (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileBrowseRead]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        prefix=prefix,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    prefix: str | Unset = "",
    limit: int | Unset = 200,
) -> ErrorResponse | FileBrowseRead | None:
    """List the folders and files under a prefix of an ADLS Gen2 / S3 / GCS connection

    Args:
        connection_id (UUID):
        prefix (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileBrowseRead
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
        prefix=prefix,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    prefix: str | Unset = "",
    limit: int | Unset = 200,
) -> Response[ErrorResponse | FileBrowseRead]:
    """List the folders and files under a prefix of an ADLS Gen2 / S3 / GCS connection

    Args:
        connection_id (UUID):
        prefix (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileBrowseRead]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        prefix=prefix,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    prefix: str | Unset = "",
    limit: int | Unset = 200,
) -> ErrorResponse | FileBrowseRead | None:
    """List the folders and files under a prefix of an ADLS Gen2 / S3 / GCS connection

    Args:
        connection_id (UUID):
        prefix (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileBrowseRead
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
            prefix=prefix,
            limit=limit,
        )
    ).parsed
