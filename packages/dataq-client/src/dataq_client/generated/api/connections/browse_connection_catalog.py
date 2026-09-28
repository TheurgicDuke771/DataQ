from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.catalog_browse_read import CatalogBrowseRead
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    connection_id: UUID,
    *,
    catalog: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_catalog: None | str | Unset
    if isinstance(catalog, Unset):
        json_catalog = UNSET
    else:
        json_catalog = catalog
    params["catalog"] = json_catalog

    json_schema: None | str | Unset
    if isinstance(schema, Unset):
        json_schema = UNSET
    else:
        json_schema = schema
    params["schema"] = json_schema

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/connections/{connection_id}/browse/catalog".format(
            connection_id=quote(str(connection_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CatalogBrowseRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CatalogBrowseRead.from_dict(response.json())

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
) -> Response[CatalogBrowseRead | HTTPValidationError]:
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
    catalog: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[CatalogBrowseRead | HTTPValidationError]:
    """List one level of a Unity Catalog (catalogs/schemas/tables) or generic SQL (schemas/tables)
    connection

    Args:
        connection_id (UUID):
        catalog (None | str | Unset):
        schema (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CatalogBrowseRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        catalog=catalog,
        schema=schema,
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
    catalog: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> CatalogBrowseRead | HTTPValidationError | None:
    """List one level of a Unity Catalog (catalogs/schemas/tables) or generic SQL (schemas/tables)
    connection

    Args:
        connection_id (UUID):
        catalog (None | str | Unset):
        schema (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CatalogBrowseRead | HTTPValidationError
    """

    return sync_detailed(
        connection_id=connection_id,
        client=client,
        catalog=catalog,
        schema=schema,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    catalog: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> Response[CatalogBrowseRead | HTTPValidationError]:
    """List one level of a Unity Catalog (catalogs/schemas/tables) or generic SQL (schemas/tables)
    connection

    Args:
        connection_id (UUID):
        catalog (None | str | Unset):
        schema (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CatalogBrowseRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
        catalog=catalog,
        schema=schema,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    connection_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    catalog: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    limit: int | Unset = 200,
) -> CatalogBrowseRead | HTTPValidationError | None:
    """List one level of a Unity Catalog (catalogs/schemas/tables) or generic SQL (schemas/tables)
    connection

    Args:
        connection_id (UUID):
        catalog (None | str | Unset):
        schema (None | str | Unset):
        limit (int | Unset):  Default: 200.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CatalogBrowseRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            connection_id=connection_id,
            client=client,
            catalog=catalog,
            schema=schema,
            limit=limit,
        )
    ).parsed
