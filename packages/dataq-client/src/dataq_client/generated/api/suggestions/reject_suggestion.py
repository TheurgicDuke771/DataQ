from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.suggestion_read import SuggestionRead
from ...types import Response


def _get_kwargs(
    suggestion_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suggestions/{suggestion_id}/reject".format(
            suggestion_id=quote(str(suggestion_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SuggestionRead | None:
    if response.status_code == 200:
        response_200 = SuggestionRead.from_dict(response.json())

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
) -> Response[ErrorResponse | SuggestionRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suggestion_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SuggestionRead]:
    """Reject a proposed rule; it is not proposed again

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SuggestionRead]
    """

    kwargs = _get_kwargs(
        suggestion_id=suggestion_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suggestion_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SuggestionRead | None:
    """Reject a proposed rule; it is not proposed again

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SuggestionRead
    """

    return sync_detailed(
        suggestion_id=suggestion_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    suggestion_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SuggestionRead]:
    """Reject a proposed rule; it is not proposed again

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SuggestionRead]
    """

    kwargs = _get_kwargs(
        suggestion_id=suggestion_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suggestion_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SuggestionRead | None:
    """Reject a proposed rule; it is not proposed again

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SuggestionRead
    """

    return (
        await asyncio_detailed(
            suggestion_id=suggestion_id,
            client=client,
        )
    ).parsed
