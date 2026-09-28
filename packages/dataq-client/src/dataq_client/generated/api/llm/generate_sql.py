from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.llm_invocation_queued import LlmInvocationQueued
from ...models.sql_generation_request import SqlGenerationRequest
from ...types import Response


def _get_kwargs(
    *,
    body: SqlGenerationRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/llm/sql_generation",
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
    body: SqlGenerationRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Generate a custom-SQL check from a natural-language rule (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged). `additional_tables` (#1649)
    joins in up to `llm_sqlgen.MAX_ADDITIONAL_TABLES` more tables on this SAME
    connection for a cross-table rule — cross-connection is refused
    structurally (there is no connection reference to give one).

    Args:
        body (SqlGenerationRequest):

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
    body: SqlGenerationRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Generate a custom-SQL check from a natural-language rule (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged). `additional_tables` (#1649)
    joins in up to `llm_sqlgen.MAX_ADDITIONAL_TABLES` more tables on this SAME
    connection for a cross-table rule — cross-connection is refused
    structurally (there is no connection reference to give one).

    Args:
        body (SqlGenerationRequest):

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
    body: SqlGenerationRequest,
) -> Response[HTTPValidationError | LlmInvocationQueued]:
    """Generate a custom-SQL check from a natural-language rule (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged). `additional_tables` (#1649)
    joins in up to `llm_sqlgen.MAX_ADDITIONAL_TABLES` more tables on this SAME
    connection for a cross-table rule — cross-connection is refused
    structurally (there is no connection reference to give one).

    Args:
        body (SqlGenerationRequest):

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
    body: SqlGenerationRequest,
) -> HTTPValidationError | LlmInvocationQueued | None:
    """Generate a custom-SQL check from a natural-language rule (suite edit)

     Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged). `additional_tables` (#1649)
    joins in up to `llm_sqlgen.MAX_ADDITIONAL_TABLES` more tables on this SAME
    connection for a cross-table rule — cross-connection is refused
    structurally (there is no connection reference to give one).

    Args:
        body (SqlGenerationRequest):

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
