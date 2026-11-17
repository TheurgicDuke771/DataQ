from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.classification_dismiss_update import ClassificationDismissUpdate
from ...models.detected_classification_read import DetectedClassificationRead
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    asset_id: UUID,
    *,
    body: ClassificationDismissUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/assets/{asset_id}/classifications".format(
            asset_id=quote(str(asset_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DetectedClassificationRead | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DetectedClassificationRead.from_dict(response.json())

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
) -> Response[DetectedClassificationRead | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> Response[DetectedClassificationRead | ErrorResponse]:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4). The column travels in
            the body: a column name may hold `/` (quoted identifiers, file headers), which a path
            segment cannot carry.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DetectedClassificationRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> DetectedClassificationRead | ErrorResponse | None:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4). The column travels in
            the body: a column name may hold `/` (quoted identifiers, file headers), which a path
            segment cannot carry.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DetectedClassificationRead | ErrorResponse
    """

    return sync_detailed(
        asset_id=asset_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> Response[DetectedClassificationRead | ErrorResponse]:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4). The column travels in
            the body: a column name may hold `/` (quoted identifiers, file headers), which a path
            segment cannot carry.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DetectedClassificationRead | ErrorResponse]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    asset_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> DetectedClassificationRead | ErrorResponse | None:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4). The column travels in
            the body: a column name may hold `/` (quoted identifiers, file headers), which a path
            segment cannot carry.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DetectedClassificationRead | ErrorResponse
    """

    return (
        await asyncio_detailed(
            asset_id=asset_id,
            client=client,
            body=body,
        )
    ).parsed
