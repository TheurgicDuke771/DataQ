from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.llm_settings_read import LlmSettingsRead
from ...models.llm_settings_update import LlmSettingsUpdate
from ...types import Response


def _get_kwargs(
    *,
    body: LlmSettingsUpdate,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/api/v1/admin/llm",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | LlmSettingsRead | None:
    if response.status_code == 200:
        response_200 = LlmSettingsRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | LlmSettingsRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: LlmSettingsUpdate,
) -> Response[HTTPValidationError | LlmSettingsRead]:
    """Save the outbound-LLM provider (admin)

     An enabled config is live-tested first (the `POST /admin/llm/test` probe) and refused with
    422 `llm_test_failed_on_save` if it fails (#1927); a disabled one is saved untested.

    Args:
        body (LlmSettingsUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LlmSettingsRead]
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
    body: LlmSettingsUpdate,
) -> HTTPValidationError | LlmSettingsRead | None:
    """Save the outbound-LLM provider (admin)

     An enabled config is live-tested first (the `POST /admin/llm/test` probe) and refused with
    422 `llm_test_failed_on_save` if it fails (#1927); a disabled one is saved untested.

    Args:
        body (LlmSettingsUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LlmSettingsRead
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: LlmSettingsUpdate,
) -> Response[HTTPValidationError | LlmSettingsRead]:
    """Save the outbound-LLM provider (admin)

     An enabled config is live-tested first (the `POST /admin/llm/test` probe) and refused with
    422 `llm_test_failed_on_save` if it fails (#1927); a disabled one is saved untested.

    Args:
        body (LlmSettingsUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | LlmSettingsRead]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: LlmSettingsUpdate,
) -> HTTPValidationError | LlmSettingsRead | None:
    """Save the outbound-LLM provider (admin)

     An enabled config is live-tested first (the `POST /admin/llm/test` probe) and refused with
    422 `llm_test_failed_on_save` if it fails (#1927); a disabled one is saved untested.

    Args:
        body (LlmSettingsUpdate):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | LlmSettingsRead
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
