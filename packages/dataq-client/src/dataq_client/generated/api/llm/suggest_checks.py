from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.check_suggestion_request import CheckSuggestionRequest
from ...models.http_validation_error import HTTPValidationError
from ...models.llm_invocation_queued import LlmInvocationQueued
from ...types import Response


def _get_kwargs(
    *,
    body: CheckSuggestionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/llm/check_suggestions",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | LlmInvocationQueued | None:
    if response.status_code == 202:
        response_202 = LlmInvocationQueued.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CheckSuggestionRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Suggest checks for a suite from its column profile (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. Every
    suggested check passes the same validator a human's `create_check` call
    would before it is ever stored — one that fails is dropped, not surfaced;
    see the invocation's `rejected` field for what didn't make it and why.

    If EVERY suggestion is rejected, the invocation fails instead — there is no
    empty-but-successful outcome for "nothing runnable came back" — and the
    top-level `rejected` shape above never gets written. The reasons are still
    readable, in two forms: folded into `error` as one summary sentence, and
    as a structured `{rejected, rejected_count, truncated}` object under
    `response` (unlike a successful run, where `response` is `{suggestions,
    rejected, coverage_warnings}`) — a failed invocation's `response` field
    carries only that narrower rejection detail, not the full success shape.
    `rejected` itself may be shorter than `rejected_count`; `truncated` is
    `true` when it is, so a caller reading only `response` (not `error`'s own
    "(+N more)" text) still has a signal the list isn't exhaustive.

    Args:
        body (CheckSuggestionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LlmInvocationQueued]
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
    body: CheckSuggestionRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Suggest checks for a suite from its column profile (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. Every
    suggested check passes the same validator a human's `create_check` call
    would before it is ever stored — one that fails is dropped, not surfaced;
    see the invocation's `rejected` field for what didn't make it and why.

    If EVERY suggestion is rejected, the invocation fails instead — there is no
    empty-but-successful outcome for "nothing runnable came back" — and the
    top-level `rejected` shape above never gets written. The reasons are still
    readable, in two forms: folded into `error` as one summary sentence, and
    as a structured `{rejected, rejected_count, truncated}` object under
    `response` (unlike a successful run, where `response` is `{suggestions,
    rejected, coverage_warnings}`) — a failed invocation's `response` field
    carries only that narrower rejection detail, not the full success shape.
    `rejected` itself may be shorter than `rejected_count`; `truncated` is
    `true` when it is, so a caller reading only `response` (not `error`'s own
    "(+N more)" text) still has a signal the list isn't exhaustive.

    Args:
        body (CheckSuggestionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LlmInvocationQueued
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CheckSuggestionRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Suggest checks for a suite from its column profile (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. Every
    suggested check passes the same validator a human's `create_check` call
    would before it is ever stored — one that fails is dropped, not surfaced;
    see the invocation's `rejected` field for what didn't make it and why.

    If EVERY suggestion is rejected, the invocation fails instead — there is no
    empty-but-successful outcome for "nothing runnable came back" — and the
    top-level `rejected` shape above never gets written. The reasons are still
    readable, in two forms: folded into `error` as one summary sentence, and
    as a structured `{rejected, rejected_count, truncated}` object under
    `response` (unlike a successful run, where `response` is `{suggestions,
    rejected, coverage_warnings}`) — a failed invocation's `response` field
    carries only that narrower rejection detail, not the full success shape.
    `rejected` itself may be shorter than `rejected_count`; `truncated` is
    `true` when it is, so a caller reading only `response` (not `error`'s own
    "(+N more)" text) still has a signal the list isn't exhaustive.

    Args:
        body (CheckSuggestionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LlmInvocationQueued]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CheckSuggestionRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Suggest checks for a suite from its column profile (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. Every
    suggested check passes the same validator a human's `create_check` call
    would before it is ever stored — one that fails is dropped, not surfaced;
    see the invocation's `rejected` field for what didn't make it and why.

    If EVERY suggestion is rejected, the invocation fails instead — there is no
    empty-but-successful outcome for "nothing runnable came back" — and the
    top-level `rejected` shape above never gets written. The reasons are still
    readable, in two forms: folded into `error` as one summary sentence, and
    as a structured `{rejected, rejected_count, truncated}` object under
    `response` (unlike a successful run, where `response` is `{suggestions,
    rejected, coverage_warnings}`) — a failed invocation's `response` field
    carries only that narrower rejection detail, not the full success shape.
    `rejected` itself may be shorter than `rejected_count`; `truncated` is
    `true` when it is, so a caller reading only `response` (not `error`'s own
    "(+N more)" text) still has a signal the list isn't exhaustive.

    Args:
        body (CheckSuggestionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LlmInvocationQueued
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
