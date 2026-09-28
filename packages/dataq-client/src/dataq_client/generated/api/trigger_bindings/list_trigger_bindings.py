from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.trigger_binding_read import TriggerBindingRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    suite_id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_provider: None | str | Unset
    if isinstance(provider, Unset):
        json_provider = UNSET
    else:
        json_provider = provider
    params["provider"] = json_provider

    json_env: None | str | Unset
    if isinstance(env, Unset):
        json_env = UNSET
    else:
        json_env = env
    params["env"] = json_env

    json_suite_id: None | str | Unset
    if isinstance(suite_id, Unset):
        json_suite_id = UNSET
    elif isinstance(suite_id, UUID):
        json_suite_id = str(suite_id)
    else:
        json_suite_id = suite_id
    params["suite_id"] = json_suite_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/trigger-bindings",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[TriggerBindingRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = TriggerBindingRead.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[TriggerBindingRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    suite_id: None | Unset | UUID = UNSET,
) -> Response[HTTPValidationError | list[TriggerBindingRead]]:
    """List trigger bindings on accessible suites

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        suite_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[TriggerBindingRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        env=env,
        suite_id=suite_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    suite_id: None | Unset | UUID = UNSET,
) -> HTTPValidationError | list[TriggerBindingRead] | None:
    """List trigger bindings on accessible suites

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        suite_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[TriggerBindingRead]
    """

    return sync_detailed(
        client=client,
        provider=provider,
        env=env,
        suite_id=suite_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    suite_id: None | Unset | UUID = UNSET,
) -> Response[HTTPValidationError | list[TriggerBindingRead]]:
    """List trigger bindings on accessible suites

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        suite_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[TriggerBindingRead]]
    """

    kwargs = _get_kwargs(
        provider=provider,
        env=env,
        suite_id=suite_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    provider: None | str | Unset = UNSET,
    env: None | str | Unset = UNSET,
    suite_id: None | Unset | UUID = UNSET,
) -> HTTPValidationError | list[TriggerBindingRead] | None:
    """List trigger bindings on accessible suites

    Args:
        provider (None | str | Unset):
        env (None | str | Unset):
        suite_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[TriggerBindingRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            provider=provider,
            env=env,
            suite_id=suite_id,
        )
    ).parsed
