from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.columns_read import ColumnsRead
from ...models.http_validation_error import HTTPValidationError
from ...models.list_columns_file_format_type_0 import ListColumnsFileFormatType0
from ...types import UNSET, Response, Unset


def _get_kwargs(
    suite_id: UUID,
    *,
    table: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    catalog: None | str | Unset = UNSET,
    namespace: None | str | Unset = UNSET,
    path: None | str | Unset = UNSET,
    file_format: ListColumnsFileFormatType0 | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_table: None | str | Unset
    if isinstance(table, Unset):
        json_table = UNSET
    else:
        json_table = table
    params["table"] = json_table

    json_schema: None | str | Unset
    if isinstance(schema, Unset):
        json_schema = UNSET
    else:
        json_schema = schema
    params["schema"] = json_schema

    json_catalog: None | str | Unset
    if isinstance(catalog, Unset):
        json_catalog = UNSET
    else:
        json_catalog = catalog
    params["catalog"] = json_catalog

    json_namespace: None | str | Unset
    if isinstance(namespace, Unset):
        json_namespace = UNSET
    else:
        json_namespace = namespace
    params["namespace"] = json_namespace

    json_path: None | str | Unset
    if isinstance(path, Unset):
        json_path = UNSET
    else:
        json_path = path
    params["path"] = json_path

    json_file_format: None | str | Unset
    if isinstance(file_format, Unset):
        json_file_format = UNSET
    elif isinstance(file_format, ListColumnsFileFormatType0):
        json_file_format = file_format.value
    else:
        json_file_format = file_format
    params["file_format"] = json_file_format

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/suites/{suite_id}/columns".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ColumnsRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ColumnsRead.from_dict(response.json())

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
) -> Response[ColumnsRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    table: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    catalog: None | str | Unset = UNSET,
    namespace: None | str | Unset = UNSET,
    path: None | str | Unset = UNSET,
    file_format: ListColumnsFileFormatType0 | None | Unset = UNSET,
) -> Response[ColumnsRead | HTTPValidationError]:
    """List the column names of a table/file on the suite's connection

    Args:
        suite_id (UUID):
        table (None | str | Unset):
        schema (None | str | Unset):
        catalog (None | str | Unset):
        namespace (None | str | Unset):
        path (None | str | Unset):
        file_format (ListColumnsFileFormatType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ColumnsRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        table=table,
        schema=schema,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    table: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    catalog: None | str | Unset = UNSET,
    namespace: None | str | Unset = UNSET,
    path: None | str | Unset = UNSET,
    file_format: ListColumnsFileFormatType0 | None | Unset = UNSET,
) -> ColumnsRead | HTTPValidationError | None:
    """List the column names of a table/file on the suite's connection

    Args:
        suite_id (UUID):
        table (None | str | Unset):
        schema (None | str | Unset):
        catalog (None | str | Unset):
        namespace (None | str | Unset):
        path (None | str | Unset):
        file_format (ListColumnsFileFormatType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ColumnsRead | HTTPValidationError
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        table=table,
        schema=schema,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    table: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    catalog: None | str | Unset = UNSET,
    namespace: None | str | Unset = UNSET,
    path: None | str | Unset = UNSET,
    file_format: ListColumnsFileFormatType0 | None | Unset = UNSET,
) -> Response[ColumnsRead | HTTPValidationError]:
    """List the column names of a table/file on the suite's connection

    Args:
        suite_id (UUID):
        table (None | str | Unset):
        schema (None | str | Unset):
        catalog (None | str | Unset):
        namespace (None | str | Unset):
        path (None | str | Unset):
        file_format (ListColumnsFileFormatType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ColumnsRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        table=table,
        schema=schema,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    table: None | str | Unset = UNSET,
    schema: None | str | Unset = UNSET,
    catalog: None | str | Unset = UNSET,
    namespace: None | str | Unset = UNSET,
    path: None | str | Unset = UNSET,
    file_format: ListColumnsFileFormatType0 | None | Unset = UNSET,
) -> ColumnsRead | HTTPValidationError | None:
    """List the column names of a table/file on the suite's connection

    Args:
        suite_id (UUID):
        table (None | str | Unset):
        schema (None | str | Unset):
        catalog (None | str | Unset):
        namespace (None | str | Unset):
        path (None | str | Unset):
        file_format (ListColumnsFileFormatType0 | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ColumnsRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            table=table,
            schema=schema,
            catalog=catalog,
            namespace=namespace,
            path=path,
            file_format=file_format,
        )
    ).parsed
