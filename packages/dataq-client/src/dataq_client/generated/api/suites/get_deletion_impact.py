from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suite_deletion_impact_read import SuiteDeletionImpactRead
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/suites/{suite_id}/deletion_impact".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuiteDeletionImpactRead | None:
    if response.status_code == 200:
        response_200 = SuiteDeletionImpactRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuiteDeletionImpactRead]:
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
) -> Response[HTTPValidationError | SuiteDeletionImpactRead]:
    """Exact dependent counts a suite delete would destroy

    Args:
        suite_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteDeletionImpactRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | SuiteDeletionImpactRead | None:
    """Exact dependent counts a suite delete would destroy

    Args:
        suite_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteDeletionImpactRead
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | SuiteDeletionImpactRead]:
    """Exact dependent counts a suite delete would destroy

    Args:
        suite_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteDeletionImpactRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | SuiteDeletionImpactRead | None:
    """Exact dependent counts a suite delete would destroy

    Args:
        suite_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteDeletionImpactRead
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
        )
    ).parsed
