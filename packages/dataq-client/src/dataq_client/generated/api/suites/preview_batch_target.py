from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.batch_preview_read import BatchPreviewRead
from ...models.http_validation_error import HTTPValidationError
from ...models.preview_batch_target_strategy import PreviewBatchTargetStrategy
from ...types import UNSET, Response, Unset


def _get_kwargs(
    suite_id: UUID,
    *,
    pattern: str,
    strategy: PreviewBatchTargetStrategy | Unset = PreviewBatchTargetStrategy.LATEST,
    batch: None | str | Unset = UNSET,
    prefix: str | Unset = "",
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["pattern"] = pattern

    json_strategy: str | Unset = UNSET
    if not isinstance(strategy, Unset):
        json_strategy = strategy.value

    params["strategy"] = json_strategy

    json_batch: None | str | Unset
    if isinstance(batch, Unset):
        json_batch = UNSET
    else:
        json_batch = batch
    params["batch"] = json_batch

    params["prefix"] = prefix

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/suites/{suite_id}/batch-preview".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BatchPreviewRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = BatchPreviewRead.from_dict(response.json())

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
) -> Response[BatchPreviewRead | HTTPValidationError]:
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
    pattern: str,
    strategy: PreviewBatchTargetStrategy | Unset = PreviewBatchTargetStrategy.LATEST,
    batch: None | str | Unset = UNSET,
    prefix: str | Unset = "",
) -> Response[BatchPreviewRead | HTTPValidationError]:
    """Resolve a flat-file batch pattern against the live listing (no persistence)

    Args:
        suite_id (UUID):
        pattern (str):
        strategy (PreviewBatchTargetStrategy | Unset):  Default:
            PreviewBatchTargetStrategy.LATEST.
        batch (None | str | Unset):
        prefix (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BatchPreviewRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        pattern=pattern,
        strategy=strategy,
        batch=batch,
        prefix=prefix,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    pattern: str,
    strategy: PreviewBatchTargetStrategy | Unset = PreviewBatchTargetStrategy.LATEST,
    batch: None | str | Unset = UNSET,
    prefix: str | Unset = "",
) -> BatchPreviewRead | HTTPValidationError | None:
    """Resolve a flat-file batch pattern against the live listing (no persistence)

    Args:
        suite_id (UUID):
        pattern (str):
        strategy (PreviewBatchTargetStrategy | Unset):  Default:
            PreviewBatchTargetStrategy.LATEST.
        batch (None | str | Unset):
        prefix (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BatchPreviewRead | HTTPValidationError
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        pattern=pattern,
        strategy=strategy,
        batch=batch,
        prefix=prefix,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    pattern: str,
    strategy: PreviewBatchTargetStrategy | Unset = PreviewBatchTargetStrategy.LATEST,
    batch: None | str | Unset = UNSET,
    prefix: str | Unset = "",
) -> Response[BatchPreviewRead | HTTPValidationError]:
    """Resolve a flat-file batch pattern against the live listing (no persistence)

    Args:
        suite_id (UUID):
        pattern (str):
        strategy (PreviewBatchTargetStrategy | Unset):  Default:
            PreviewBatchTargetStrategy.LATEST.
        batch (None | str | Unset):
        prefix (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BatchPreviewRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        pattern=pattern,
        strategy=strategy,
        batch=batch,
        prefix=prefix,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    pattern: str,
    strategy: PreviewBatchTargetStrategy | Unset = PreviewBatchTargetStrategy.LATEST,
    batch: None | str | Unset = UNSET,
    prefix: str | Unset = "",
) -> BatchPreviewRead | HTTPValidationError | None:
    """Resolve a flat-file batch pattern against the live listing (no persistence)

    Args:
        suite_id (UUID):
        pattern (str):
        strategy (PreviewBatchTargetStrategy | Unset):  Default:
            PreviewBatchTargetStrategy.LATEST.
        batch (None | str | Unset):
        prefix (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BatchPreviewRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            pattern=pattern,
            strategy=strategy,
            batch=batch,
            prefix=prefix,
        )
    ).parsed
