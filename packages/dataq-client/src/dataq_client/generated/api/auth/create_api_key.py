from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.api_key_create import ApiKeyCreate
from ...models.api_key_created import ApiKeyCreated
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: ApiKeyCreate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/me/api-keys",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ApiKeyCreated | HTTPValidationError | None:
    if response.status_code == 201:
        response_201 = ApiKeyCreated.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ApiKeyCreated | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ApiKeyCreate,
) -> Response[ApiKeyCreated | HTTPValidationError]:
    """Mint an API key (personal access token) — plaintext shown once

     Mint a PAT that authenticates as you (`Authorization: Bearer dq_live_…`)
    on the REST API and `/mcp` alike, inheriting your per-suite access.

    Args:
        body (ApiKeyCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ApiKeyCreated | HTTPValidationError]
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
    body: ApiKeyCreate,
) -> ApiKeyCreated | HTTPValidationError | None:
    """Mint an API key (personal access token) — plaintext shown once

     Mint a PAT that authenticates as you (`Authorization: Bearer dq_live_…`)
    on the REST API and `/mcp` alike, inheriting your per-suite access.

    Args:
        body (ApiKeyCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ApiKeyCreated | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ApiKeyCreate,
) -> Response[ApiKeyCreated | HTTPValidationError]:
    """Mint an API key (personal access token) — plaintext shown once

     Mint a PAT that authenticates as you (`Authorization: Bearer dq_live_…`)
    on the REST API and `/mcp` alike, inheriting your per-suite access.

    Args:
        body (ApiKeyCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ApiKeyCreated | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ApiKeyCreate,
) -> ApiKeyCreated | HTTPValidationError | None:
    """Mint an API key (personal access token) — plaintext shown once

     Mint a PAT that authenticates as you (`Authorization: Bearer dq_live_…`)
    on the REST API and `/mcp` alike, inheriting your per-suite access.

    Args:
        body (ApiKeyCreate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ApiKeyCreated | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
