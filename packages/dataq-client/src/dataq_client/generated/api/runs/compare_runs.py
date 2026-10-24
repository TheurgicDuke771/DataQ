from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.run_comparison_read import RunComparisonRead
from ...types import Response


def _get_kwargs(
    run_id: UUID,
    other_run_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/runs/{run_id}/compare/{other_run_id}".format(
            run_id=quote(str(run_id), safe=""),
            other_run_id=quote(str(other_run_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | RunComparisonRead | None:
    if response.status_code == 200:
        response_200 = RunComparisonRead.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = cast(Any, None)
        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | RunComparisonRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    run_id: UUID,
    other_run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | RunComparisonRead]:
    """Compare two runs of a suite check by check

    Args:
        run_id (UUID):
        other_run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | RunComparisonRead]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        other_run_id=other_run_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    run_id: UUID,
    other_run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | RunComparisonRead | None:
    """Compare two runs of a suite check by check

    Args:
        run_id (UUID):
        other_run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | RunComparisonRead
    """

    return sync_detailed(
        run_id=run_id,
        other_run_id=other_run_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    run_id: UUID,
    other_run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | RunComparisonRead]:
    """Compare two runs of a suite check by check

    Args:
        run_id (UUID):
        other_run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | RunComparisonRead]
    """

    kwargs = _get_kwargs(
        run_id=run_id,
        other_run_id=other_run_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    run_id: UUID,
    other_run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | RunComparisonRead | None:
    """Compare two runs of a suite check by check

    Args:
        run_id (UUID):
        other_run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | RunComparisonRead
    """

    return (
        await asyncio_detailed(
            run_id=run_id,
            other_run_id=other_run_id,
            client=client,
        )
    ).parsed
