from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.offboard_receipt_read import OffboardReceiptRead
from ...models.offboard_request import OffboardRequest
from ...types import Response


def _get_kwargs(
    user_id: UUID,
    *,
    body: OffboardRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/offboarding/{user_id}".format(
            user_id=quote(str(user_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OffboardReceiptRead | None:
    if response.status_code == 200:
        response_200 = OffboardReceiptRead.from_dict(response.json())

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
) -> Response[ErrorResponse | OffboardReceiptRead]:
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
    body: OffboardRequest,
) -> Response[ErrorResponse | OffboardReceiptRead]:
    """Offboard a user in one audited pass (admin)

     Transfer every suite this user owns, revoke every PAT and browser session
    they hold, then withdraw their workspace membership — in one transaction.

    Their authored history stays: runs, results and checks keep pointing at them,
    and the user row itself is not deleted (erasure is the data-subject-rights
    endpoint, which is a different act). A step that cannot run is reported in
    `skipped` with its reason rather than silently passing.

    Args:
        user_id (UUID):
        body (OffboardRequest): `POST /admin/offboarding/{user_id}` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OffboardReceiptRead]
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
    body: OffboardRequest,
) -> ErrorResponse | OffboardReceiptRead | None:
    """Offboard a user in one audited pass (admin)

     Transfer every suite this user owns, revoke every PAT and browser session
    they hold, then withdraw their workspace membership — in one transaction.

    Their authored history stays: runs, results and checks keep pointing at them,
    and the user row itself is not deleted (erasure is the data-subject-rights
    endpoint, which is a different act). A step that cannot run is reported in
    `skipped` with its reason rather than silently passing.

    Args:
        user_id (UUID):
        body (OffboardRequest): `POST /admin/offboarding/{user_id}` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OffboardReceiptRead
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
    body: OffboardRequest,
) -> Response[ErrorResponse | OffboardReceiptRead]:
    """Offboard a user in one audited pass (admin)

     Transfer every suite this user owns, revoke every PAT and browser session
    they hold, then withdraw their workspace membership — in one transaction.

    Their authored history stays: runs, results and checks keep pointing at them,
    and the user row itself is not deleted (erasure is the data-subject-rights
    endpoint, which is a different act). A step that cannot run is reported in
    `skipped` with its reason rather than silently passing.

    Args:
        user_id (UUID):
        body (OffboardRequest): `POST /admin/offboarding/{user_id}` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OffboardReceiptRead]
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
    body: OffboardRequest,
) -> ErrorResponse | OffboardReceiptRead | None:
    """Offboard a user in one audited pass (admin)

     Transfer every suite this user owns, revoke every PAT and browser session
    they hold, then withdraw their workspace membership — in one transaction.

    Their authored history stays: runs, results and checks keep pointing at them,
    and the user row itself is not deleted (erasure is the data-subject-rights
    endpoint, which is a different act). A step that cannot run is reported in
    `skipped` with its reason rather than silently passing.

    Args:
        user_id (UUID):
        body (OffboardRequest): `POST /admin/offboarding/{user_id}` body.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OffboardReceiptRead
    """

    return (
        await asyncio_detailed(
            user_id=user_id,
            client=client,
            body=body,
        )
    ).parsed
