from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suite_transfer import SuiteTransfer
from ...models.suite_transfer_result import SuiteTransferResult
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    *,
    body: SuiteTransfer,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/suites/{suite_id}/transfer".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuiteTransferResult | None:
    if response.status_code == 200:
        response_200 = SuiteTransferResult.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuiteTransferResult]:
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
    body: SuiteTransfer,
) -> Response[HTTPValidationError | SuiteTransferResult]:
    """Transfer suite ownership (admin)

     Hand a suite to another user — the offboarding primitive.

    Args:
        suite_id (UUID):
        body (SuiteTransfer): `POST /admin/suites/{id}/transfer` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteTransferResult]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteTransfer,
) -> HTTPValidationError | SuiteTransferResult | None:
    """Transfer suite ownership (admin)

     Hand a suite to another user — the offboarding primitive.

    Args:
        suite_id (UUID):
        body (SuiteTransfer): `POST /admin/suites/{id}/transfer` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteTransferResult
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteTransfer,
) -> Response[HTTPValidationError | SuiteTransferResult]:
    """Transfer suite ownership (admin)

     Hand a suite to another user — the offboarding primitive.

    Args:
        suite_id (UUID):
        body (SuiteTransfer): `POST /admin/suites/{id}/transfer` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteTransferResult]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteTransfer,
) -> HTTPValidationError | SuiteTransferResult | None:
    """Transfer suite ownership (admin)

     Hand a suite to another user — the offboarding primitive.

    Args:
        suite_id (UUID):
        body (SuiteTransfer): `POST /admin/suites/{id}/transfer` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteTransferResult
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            body=body,
        )
    ).parsed
