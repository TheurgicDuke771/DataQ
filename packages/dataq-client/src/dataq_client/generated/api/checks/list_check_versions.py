from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.check_version_read import CheckVersionRead
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    check_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/suites/{suite_id}/checks/{check_id}/versions".format(
            suite_id=quote(str(suite_id), safe=""),
            check_id=quote(str(check_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[CheckVersionRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = CheckVersionRead.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[HTTPValidationError | list[CheckVersionRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | list[CheckVersionRead]]:
    """List a check's version history (newest first)

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[CheckVersionRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | list[CheckVersionRead] | None:
    """List a check's version history (newest first)

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[CheckVersionRead]
    """

    return sync_detailed(
        suite_id=suite_id,
        check_id=check_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | list[CheckVersionRead]]:
    """List a check's version history (newest first)

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[CheckVersionRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    check_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | list[CheckVersionRead] | None:
    """List a check's version history (newest first)

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[CheckVersionRead]
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            check_id=check_id,
            client=client,
        )
    ).parsed
