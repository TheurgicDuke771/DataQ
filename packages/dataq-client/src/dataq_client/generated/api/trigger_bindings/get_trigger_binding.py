from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.trigger_binding_read import TriggerBindingRead
from ...types import Response


def _get_kwargs(
    binding_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/trigger-bindings/{binding_id}".format(
            binding_id=quote(str(binding_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | TriggerBindingRead | None:
    if response.status_code == 200:
        response_200 = TriggerBindingRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | TriggerBindingRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    binding_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | TriggerBindingRead]:
    """Get a trigger binding

    Args:
        binding_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TriggerBindingRead]
    """

    kwargs = _get_kwargs(
        binding_id=binding_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    binding_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | TriggerBindingRead | None:
    """Get a trigger binding

    Args:
        binding_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TriggerBindingRead
    """

    return sync_detailed(
        binding_id=binding_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    binding_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | TriggerBindingRead]:
    """Get a trigger binding

    Args:
        binding_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | TriggerBindingRead]
    """

    kwargs = _get_kwargs(
        binding_id=binding_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    binding_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | TriggerBindingRead | None:
    """Get a trigger binding

    Args:
        binding_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | TriggerBindingRead
    """

    return (
        await asyncio_detailed(
            binding_id=binding_id,
            client=client,
        )
    ).parsed
