from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.webhook_regenerate_response import WebhookRegenerateResponse
from ...types import Response


def _get_kwargs(
    provider: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/orchestration/webhooks/{provider}/regenerate".format(
            provider=quote(str(provider), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WebhookRegenerateResponse | None:
    if response.status_code == 200:
        response_200 = WebhookRegenerateResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | WebhookRegenerateResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    provider: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | WebhookRegenerateResponse]:
    """Regenerate a provider's inbound-webhook secret/key (admin)

     Mint a new secret for one orchestration provider's inbound webhook.

    The previous value keeps working until `grace_until` (`WEBHOOK_SECRET_GRACE_MINUTES`,
    15 by default), so updating the provider side is not a race — but DataQ cannot tell
    whether that update happened, so nothing here confirms the provider is still able to
    deliver. Once the window closes, callbacks signed with the old value are rejected.

    The audit row records the provider and the grace window, never the value.

    Args:
        provider (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebhookRegenerateResponse]
    """

    kwargs = _get_kwargs(
        provider=provider,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    provider: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | WebhookRegenerateResponse | None:
    """Regenerate a provider's inbound-webhook secret/key (admin)

     Mint a new secret for one orchestration provider's inbound webhook.

    The previous value keeps working until `grace_until` (`WEBHOOK_SECRET_GRACE_MINUTES`,
    15 by default), so updating the provider side is not a race — but DataQ cannot tell
    whether that update happened, so nothing here confirms the provider is still able to
    deliver. Once the window closes, callbacks signed with the old value are rejected.

    The audit row records the provider and the grace window, never the value.

    Args:
        provider (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebhookRegenerateResponse
    """

    return sync_detailed(
        provider=provider,
        client=client,
    ).parsed


async def asyncio_detailed(
    provider: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | WebhookRegenerateResponse]:
    """Regenerate a provider's inbound-webhook secret/key (admin)

     Mint a new secret for one orchestration provider's inbound webhook.

    The previous value keeps working until `grace_until` (`WEBHOOK_SECRET_GRACE_MINUTES`,
    15 by default), so updating the provider side is not a race — but DataQ cannot tell
    whether that update happened, so nothing here confirms the provider is still able to
    deliver. Once the window closes, callbacks signed with the old value are rejected.

    The audit row records the provider and the grace window, never the value.

    Args:
        provider (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebhookRegenerateResponse]
    """

    kwargs = _get_kwargs(
        provider=provider,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    provider: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | WebhookRegenerateResponse | None:
    """Regenerate a provider's inbound-webhook secret/key (admin)

     Mint a new secret for one orchestration provider's inbound webhook.

    The previous value keeps working until `grace_until` (`WEBHOOK_SECRET_GRACE_MINUTES`,
    15 by default), so updating the provider side is not a race — but DataQ cannot tell
    whether that update happened, so nothing here confirms the provider is still able to
    deliver. Once the window closes, callbacks signed with the old value are rejected.

    The audit row records the provider and the grace window, never the value.

    Args:
        provider (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebhookRegenerateResponse
    """

    return (
        await asyncio_detailed(
            provider=provider,
            client=client,
        )
    ).parsed
