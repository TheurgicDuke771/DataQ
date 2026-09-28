from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.schedule_read import ScheduleRead
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    suite_id: None | Unset | UUID = UNSET,
    enabled: bool | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_suite_id: None | str | Unset
    if isinstance(suite_id, Unset):
        json_suite_id = UNSET
    elif isinstance(suite_id, UUID):
        json_suite_id = str(suite_id)
    else:
        json_suite_id = suite_id
    params["suite_id"] = json_suite_id

    json_enabled: bool | None | Unset
    if isinstance(enabled, Unset):
        json_enabled = UNSET
    else:
        json_enabled = enabled
    params["enabled"] = json_enabled

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/schedules",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[ScheduleRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ScheduleRead.from_dict(response_200_item_data)

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
) -> Response[HTTPValidationError | list[ScheduleRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    enabled: bool | None | Unset = UNSET,
) -> Response[HTTPValidationError | list[ScheduleRead]]:
    """List schedules on accessible suites

    Args:
        suite_id (None | Unset | UUID):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ScheduleRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        enabled=enabled,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    enabled: bool | None | Unset = UNSET,
) -> HTTPValidationError | list[ScheduleRead] | None:
    """List schedules on accessible suites

    Args:
        suite_id (None | Unset | UUID):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ScheduleRead]
    """

    return sync_detailed(
        client=client,
        suite_id=suite_id,
        enabled=enabled,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    enabled: bool | None | Unset = UNSET,
) -> Response[HTTPValidationError | list[ScheduleRead]]:
    """List schedules on accessible suites

    Args:
        suite_id (None | Unset | UUID):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[ScheduleRead]]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        enabled=enabled,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    suite_id: None | Unset | UUID = UNSET,
    enabled: bool | None | Unset = UNSET,
) -> HTTPValidationError | list[ScheduleRead] | None:
    """List schedules on accessible suites

    Args:
        suite_id (None | Unset | UUID):
        enabled (bool | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[ScheduleRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            suite_id=suite_id,
            enabled=enabled,
        )
    ).parsed
