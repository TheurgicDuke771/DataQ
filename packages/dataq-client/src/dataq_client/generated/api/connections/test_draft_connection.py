from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connection_draft_test import ConnectionDraftTest
from ...models.connection_test_result import ConnectionTestResult
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: ConnectionDraftTest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/connections/test",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectionTestResult | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ConnectionTestResult.from_dict(response.json())

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
) -> Response[ConnectionTestResult | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ConnectionDraftTest,
) -> Response[ConnectionTestResult | HTTPValidationError]:
    """Test live connectivity for an unsaved draft connection

     Probe the config/secret the user just typed — before Create is pressed.

    Args:
        body (ConnectionDraftTest): The payload for `/connections/test` — everything
            `ConnectionCreate` needs to probe
            connectivity, minus `name` (a draft has no row and needs none).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionTestResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: ConnectionDraftTest,
) -> ConnectionTestResult | HTTPValidationError | None:
    """Test live connectivity for an unsaved draft connection

     Probe the config/secret the user just typed — before Create is pressed.

    Args:
        body (ConnectionDraftTest): The payload for `/connections/test` — everything
            `ConnectionCreate` needs to probe
            connectivity, minus `name` (a draft has no row and needs none).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionTestResult | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ConnectionDraftTest,
) -> Response[ConnectionTestResult | HTTPValidationError]:
    """Test live connectivity for an unsaved draft connection

     Probe the config/secret the user just typed — before Create is pressed.

    Args:
        body (ConnectionDraftTest): The payload for `/connections/test` — everything
            `ConnectionCreate` needs to probe
            connectivity, minus `name` (a draft has no row and needs none).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectionTestResult | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ConnectionDraftTest,
) -> ConnectionTestResult | HTTPValidationError | None:
    """Test live connectivity for an unsaved draft connection

     Probe the config/secret the user just typed — before Create is pressed.

    Args:
        body (ConnectionDraftTest): The payload for `/connections/test` — everything
            `ConnectionCreate` needs to probe
            connectivity, minus `name` (a draft has no row and needs none).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectionTestResult | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
