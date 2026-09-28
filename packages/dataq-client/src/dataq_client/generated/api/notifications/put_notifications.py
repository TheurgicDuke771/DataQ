from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suite_notification_read import SuiteNotificationRead
from ...models.suite_notification_update import SuiteNotificationUpdate
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    *,
    body: SuiteNotificationUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/v1/suites/{suite_id}/notifications".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuiteNotificationRead | None:
    if response.status_code == 200:
        response_200 = SuiteNotificationRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuiteNotificationRead]:
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
    body: SuiteNotificationUpdate,
) -> Response[HTTPValidationError | SuiteNotificationRead]:
    """Create or update a suite's notification config

    Args:
        suite_id (UUID):
        body (SuiteNotificationUpdate): `enabled`/`alert_on` plus channel links are the whole
            config (#1926). The
            three inline destinations below are accepted only as "" (clear a legacy
            value); any other value is refused for every caller.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteNotificationRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteNotificationUpdate,
) -> HTTPValidationError | SuiteNotificationRead | None:
    """Create or update a suite's notification config

    Args:
        suite_id (UUID):
        body (SuiteNotificationUpdate): `enabled`/`alert_on` plus channel links are the whole
            config (#1926). The
            three inline destinations below are accepted only as "" (clear a legacy
            value); any other value is refused for every caller.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteNotificationRead
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteNotificationUpdate,
) -> Response[HTTPValidationError | SuiteNotificationRead]:
    """Create or update a suite's notification config

    Args:
        suite_id (UUID):
        body (SuiteNotificationUpdate): `enabled`/`alert_on` plus channel links are the whole
            config (#1926). The
            three inline destinations below are accepted only as "" (clear a legacy
            value); any other value is refused for every caller.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteNotificationRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteNotificationUpdate,
) -> HTTPValidationError | SuiteNotificationRead | None:
    """Create or update a suite's notification config

    Args:
        suite_id (UUID):
        body (SuiteNotificationUpdate): `enabled`/`alert_on` plus channel links are the whole
            config (#1926). The
            three inline destinations below are accepted only as "" (clear a legacy
            value); any other value is refused for every caller.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteNotificationRead
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            body=body,
        )
    ).parsed
