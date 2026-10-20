from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.classification_dismiss_update import ClassificationDismissUpdate
from ...models.detected_classification_read import DetectedClassificationRead
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    asset_id: UUID,
    column: str,
    *,
    body: ClassificationDismissUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/assets/{asset_id}/classifications/{column}".format(
            asset_id=quote(str(asset_id), safe=""),
            column=quote(str(column), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DetectedClassificationRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = DetectedClassificationRead.from_dict(response.json())

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
) -> Response[DetectedClassificationRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    asset_id: UUID,
    column: str,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> Response[DetectedClassificationRead | HTTPValidationError]:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        column (str):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DetectedClassificationRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        column=column,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    asset_id: UUID,
    column: str,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> DetectedClassificationRead | HTTPValidationError | None:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        column (str):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DetectedClassificationRead | HTTPValidationError
    """

    return sync_detailed(
        asset_id=asset_id,
        column=column,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    asset_id: UUID,
    column: str,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> Response[DetectedClassificationRead | HTTPValidationError]:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        column (str):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DetectedClassificationRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        asset_id=asset_id,
        column=column,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    asset_id: UUID,
    column: str,
    *,
    client: AuthenticatedClient | Client,
    body: ClassificationDismissUpdate,
) -> DetectedClassificationRead | HTTPValidationError | None:
    """Dismiss or restore a profiler-detected classification (workspace-admin only)

    Args:
        asset_id (UUID):
        column (str):
        body (ClassificationDismissUpdate): Dismiss (`true`) or restore (`false`) one profiler
            detection — the only way a detection
            is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DetectedClassificationRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            asset_id=asset_id,
            column=column,
            client=client,
            body=body,
        )
    ).parsed
