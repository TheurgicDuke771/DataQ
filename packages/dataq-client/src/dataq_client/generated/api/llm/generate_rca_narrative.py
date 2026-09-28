from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.llm_invocation_queued import LlmInvocationQueued
from ...models.rca_narrative_request import RcaNarrativeRequest
from ...types import Response


def _get_kwargs(
    *,
    body: RcaNarrativeRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/llm/rca_narrative",
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
    body: RcaNarrativeRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Explain a failed check — LLM root-cause narrative on an incident's evidence card

     Queues a worker-side narrative over an incident's already-captured
    evidence card + a longer per-check history; poll `GET /llm/invocations/{id}`.
    Read-only — nothing is saved to the suite — so it's gated the same as
    reading the incident itself (`view`), not `edit`.

    If the model's every ranked hypothesis fails validation (cites no evidence
    layer from the closed vocabulary, or an invalid confidence level), the
    invocation fails instead of returning an empty narrative — same shape as
    `/check_suggestions`' all-rejected case: the reasons are folded into
    `error` and, structured, into `response` as `{rejected, rejected_count,
    truncated}`.

    Args:
        body (RcaNarrativeRequest):

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
    body: RcaNarrativeRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Explain a failed check — LLM root-cause narrative on an incident's evidence card

     Queues a worker-side narrative over an incident's already-captured
    evidence card + a longer per-check history; poll `GET /llm/invocations/{id}`.
    Read-only — nothing is saved to the suite — so it's gated the same as
    reading the incident itself (`view`), not `edit`.

    If the model's every ranked hypothesis fails validation (cites no evidence
    layer from the closed vocabulary, or an invalid confidence level), the
    invocation fails instead of returning an empty narrative — same shape as
    `/check_suggestions`' all-rejected case: the reasons are folded into
    `error` and, structured, into `response` as `{rejected, rejected_count,
    truncated}`.

    Args:
        body (RcaNarrativeRequest):

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
    body: RcaNarrativeRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Explain a failed check — LLM root-cause narrative on an incident's evidence card

     Queues a worker-side narrative over an incident's already-captured
    evidence card + a longer per-check history; poll `GET /llm/invocations/{id}`.
    Read-only — nothing is saved to the suite — so it's gated the same as
    reading the incident itself (`view`), not `edit`.

    If the model's every ranked hypothesis fails validation (cites no evidence
    layer from the closed vocabulary, or an invalid confidence level), the
    invocation fails instead of returning an empty narrative — same shape as
    `/check_suggestions`' all-rejected case: the reasons are folded into
    `error` and, structured, into `response` as `{rejected, rejected_count,
    truncated}`.

    Args:
        body (RcaNarrativeRequest):

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
    body: RcaNarrativeRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Explain a failed check — LLM root-cause narrative on an incident's evidence card

     Queues a worker-side narrative over an incident's already-captured
    evidence card + a longer per-check history; poll `GET /llm/invocations/{id}`.
    Read-only — nothing is saved to the suite — so it's gated the same as
    reading the incident itself (`view`), not `edit`.

    If the model's every ranked hypothesis fails validation (cites no evidence
    layer from the closed vocabulary, or an invalid confidence level), the
    invocation fails instead of returning an empty narrative — same shape as
    `/check_suggestions`' all-rejected case: the reasons are folded into
    `error` and, structured, into `response` as `{rejected, rejected_count,
    truncated}`.

    Args:
        body (RcaNarrativeRequest):

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
