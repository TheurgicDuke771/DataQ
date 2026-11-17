from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    check_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suites/{suite_id}/checks/{check_id}/rebaseline".format(
            suite_id=quote(str(suite_id), safe=""),
            check_id=quote(str(check_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

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
) -> Response[Any | ErrorResponse]:
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
) -> Response[Any | ErrorResponse]:
    """Drop a stateful monitor check's baseline (recaptured on the next run)

     Deliberately a delete-then-recapture-next-run, not an immediate recapture:
    recapturing here would run datasource introspection (schema_drift) or a target
    query (anomaly) on the API request thread. 204 whether or not a baseline
    existed (idempotent); 422 for a non-stateful kind (nothing to re-baseline).

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
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
) -> Any | ErrorResponse | None:
    """Drop a stateful monitor check's baseline (recaptured on the next run)

     Deliberately a delete-then-recapture-next-run, not an immediate recapture:
    recapturing here would run datasource introspection (schema_drift) or a target
    query (anomaly) on the API request thread. 204 whether or not a baseline
    existed (idempotent); 422 for a non-stateful kind (nothing to re-baseline).

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
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
) -> Response[Any | ErrorResponse]:
    """Drop a stateful monitor check's baseline (recaptured on the next run)

     Deliberately a delete-then-recapture-next-run, not an immediate recapture:
    recapturing here would run datasource introspection (schema_drift) or a target
    query (anomaly) on the API request thread. 204 whether or not a baseline
    existed (idempotent); 422 for a non-stateful kind (nothing to re-baseline).

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
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
) -> Any | ErrorResponse | None:
    """Drop a stateful monitor check's baseline (recaptured on the next run)

     Deliberately a delete-then-recapture-next-run, not an immediate recapture:
    recapturing here would run datasource introspection (schema_drift) or a target
    query (anomaly) on the API request thread. 204 whether or not a baseline
    existed (idempotent); 422 for a non-stateful kind (nothing to re-baseline).

    Args:
        suite_id (UUID):
        check_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            check_id=check_id,
            client=client,
        )
    ).parsed
