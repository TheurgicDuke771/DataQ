from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.export_suite_format import ExportSuiteFormat
from ...models.suite_document import SuiteDocument
from ...types import UNSET, Response, Unset


def _get_kwargs(
    suite_id: UUID,
    *,
    format_: ExportSuiteFormat | Unset = ExportSuiteFormat.JSON,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_format_: str | Unset = UNSET
    if not isinstance(format_, Unset):
        json_format_ = format_.value

    params["format"] = json_format_

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/suites/{suite_id}/export".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SuiteDocument | None:
    if response.status_code == 200:
        response_200 = SuiteDocument.from_dict(response.json())

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
) -> Response[ErrorResponse | SuiteDocument]:
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
    format_: ExportSuiteFormat | Unset = ExportSuiteFormat.JSON,
) -> Response[ErrorResponse | SuiteDocument]:
    """Export a suite as a portable document

    Args:
        suite_id (UUID):
        format_ (ExportSuiteFormat | Unset): `yaml` returns the same document as YAML text.
            Default: ExportSuiteFormat.JSON.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SuiteDocument]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        format_=format_,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    format_: ExportSuiteFormat | Unset = ExportSuiteFormat.JSON,
) -> ErrorResponse | SuiteDocument | None:
    """Export a suite as a portable document

    Args:
        suite_id (UUID):
        format_ (ExportSuiteFormat | Unset): `yaml` returns the same document as YAML text.
            Default: ExportSuiteFormat.JSON.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SuiteDocument
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        format_=format_,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    format_: ExportSuiteFormat | Unset = ExportSuiteFormat.JSON,
) -> Response[ErrorResponse | SuiteDocument]:
    """Export a suite as a portable document

    Args:
        suite_id (UUID):
        format_ (ExportSuiteFormat | Unset): `yaml` returns the same document as YAML text.
            Default: ExportSuiteFormat.JSON.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SuiteDocument]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        format_=format_,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    format_: ExportSuiteFormat | Unset = ExportSuiteFormat.JSON,
) -> ErrorResponse | SuiteDocument | None:
    """Export a suite as a portable document

    Args:
        suite_id (UUID):
        format_ (ExportSuiteFormat | Unset): `yaml` returns the same document as YAML text.
            Default: ExportSuiteFormat.JSON.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SuiteDocument
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            format_=format_,
        )
    ).parsed
