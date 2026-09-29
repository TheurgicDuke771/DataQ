from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suggestion_read import SuggestionRead
from ...types import Response


def _get_kwargs(
    suggestion_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suggestions/{suggestion_id}/accept".format(
            suggestion_id=quote(str(suggestion_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuggestionRead | None:
    if response.status_code == 200:
        response_200 = SuggestionRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuggestionRead]:
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
) -> Response[HTTPValidationError | SuggestionRead]:
    """Accept a proposed rule: it becomes a check in the suite

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuggestionRead]
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
) -> HTTPValidationError | SuggestionRead | None:
    """Accept a proposed rule: it becomes a check in the suite

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuggestionRead
    """

    return sync_detailed(
        suggestion_id=suggestion_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    suggestion_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | SuggestionRead]:
    """Accept a proposed rule: it becomes a check in the suite

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuggestionRead]
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
) -> HTTPValidationError | SuggestionRead | None:
    """Accept a proposed rule: it becomes a check in the suite

    Args:
        suggestion_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuggestionRead
    """

    return (
        await asyncio_detailed(
            suggestion_id=suggestion_id,
            client=client,
        )
    ).parsed
