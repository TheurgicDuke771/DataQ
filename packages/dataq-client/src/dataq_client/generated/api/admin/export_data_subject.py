from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.data_subject_export_response import DataSubjectExportResponse
from ...models.data_subject_request import DataSubjectRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: DataSubjectRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/data-subject-requests/export",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DataSubjectExportResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = DataSubjectExportResponse.from_dict(response.json())

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
) -> Response[DataSubjectExportResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> Response[DataSubjectExportResponse | HTTPValidationError]:
    """Export a data subject's captured sample data (admin only, GDPR Art 15/20)

     Workspace-wide export of every captured sample cell naming
    `column` = `value` — the access/portability half of the subject-rights
    machinery. Returns unredacted data by design (see `DataSubjectMatch`), so this
    is deliberately Admin-only, same tier as a connection credential.

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DataSubjectExportResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> DataSubjectExportResponse | HTTPValidationError | None:
    """Export a data subject's captured sample data (admin only, GDPR Art 15/20)

     Workspace-wide export of every captured sample cell naming
    `column` = `value` — the access/portability half of the subject-rights
    machinery. Returns unredacted data by design (see `DataSubjectMatch`), so this
    is deliberately Admin-only, same tier as a connection credential.

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DataSubjectExportResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> Response[DataSubjectExportResponse | HTTPValidationError]:
    """Export a data subject's captured sample data (admin only, GDPR Art 15/20)

     Workspace-wide export of every captured sample cell naming
    `column` = `value` — the access/portability half of the subject-rights
    machinery. Returns unredacted data by design (see `DataSubjectMatch`), so this
    is deliberately Admin-only, same tier as a connection credential.

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DataSubjectExportResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> DataSubjectExportResponse | HTTPValidationError | None:
    """Export a data subject's captured sample data (admin only, GDPR Art 15/20)

     Workspace-wide export of every captured sample cell naming
    `column` = `value` — the access/portability half of the subject-rights
    machinery. Returns unredacted data by design (see `DataSubjectMatch`), so this
    is deliberately Admin-only, same tier as a connection credential.

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DataSubjectExportResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
