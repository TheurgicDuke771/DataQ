from http import HTTPStatus
from io import BytesIO
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import UNSET, File, Response, Unset


def _get_kwargs(
    run_id: UUID,
    result_id: UUID,
    *,
    fmt: str | Unset = "csv",
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["fmt"] = fmt

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs/{run_id}/results/{result_id}/comparison_report".format(
            run_id=quote(str(run_id), safe=""),
            result_id=quote(str(result_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | File | None:
    if response.status_code == 200:
        response_200 = File(payload=BytesIO(response.content))

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
) -> Response[ErrorResponse | File]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: UUID,
    result_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    fmt: str | Unset = "csv",
) -> Response[ErrorResponse | File]:
    """Download a comparison result's report (derived, never stored)

     CSV/XLSX derived at request time from the persisted **redacted** buckets
    (ADR 0015 §4 — a stored file would bypass redaction and the retention
    sweep). Same authz + redaction as the run-detail read; format is chosen at
    download time, nothing persists server-side.

    Args:
        run_id (UUID):
        result_id (UUID):
        fmt (str | Unset):  Default: 'csv'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | File]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        result_id=result_id,
        fmt=fmt,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: UUID,
    result_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    fmt: str | Unset = "csv",
) -> ErrorResponse | File | None:
    """Download a comparison result's report (derived, never stored)

     CSV/XLSX derived at request time from the persisted **redacted** buckets
    (ADR 0015 §4 — a stored file would bypass redaction and the retention
    sweep). Same authz + redaction as the run-detail read; format is chosen at
    download time, nothing persists server-side.

    Args:
        run_id (UUID):
        result_id (UUID):
        fmt (str | Unset):  Default: 'csv'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | File
    """

    return sync_detailed(
        run_id=run_id,
        result_id=result_id,
        client=client,
        fmt=fmt,
    ).parsed


async def asyncio_detailed(
    run_id: UUID,
    result_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    fmt: str | Unset = "csv",
) -> Response[ErrorResponse | File]:
    """Download a comparison result's report (derived, never stored)

     CSV/XLSX derived at request time from the persisted **redacted** buckets
    (ADR 0015 §4 — a stored file would bypass redaction and the retention
    sweep). Same authz + redaction as the run-detail read; format is chosen at
    download time, nothing persists server-side.

    Args:
        run_id (UUID):
        result_id (UUID):
        fmt (str | Unset):  Default: 'csv'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | File]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        result_id=result_id,
        fmt=fmt,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: UUID,
    result_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    fmt: str | Unset = "csv",
) -> ErrorResponse | File | None:
    """Download a comparison result's report (derived, never stored)

     CSV/XLSX derived at request time from the persisted **redacted** buckets
    (ADR 0015 §4 — a stored file would bypass redaction and the retention
    sweep). Same authz + redaction as the run-detail read; format is chosen at
    download time, nothing persists server-side.

    Args:
        run_id (UUID):
        result_id (UUID):
        fmt (str | Unset):  Default: 'csv'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | File
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            result_id=result_id,
            client=client,
            fmt=fmt,
        )
    ).parsed
