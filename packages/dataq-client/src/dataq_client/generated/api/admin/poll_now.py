from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.poll_now_response import PollNowResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    connection_id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_connection_id: None | str | Unset
    if isinstance(connection_id, Unset):
        json_connection_id = UNSET
    elif isinstance(connection_id, UUID):
        json_connection_id = str(connection_id)
    else:
        json_connection_id = connection_id
    params["connection_id"] = json_connection_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/orchestration/poll-now",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PollNowResponse | None:
    if response.status_code == 202:
        response_202 = PollNowResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | PollNowResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    connection_id: None | Unset | UUID = UNSET,
) -> Response[HTTPValidationError | PollNowResponse]:
    """Run the orchestration poll now, for one connection or all (admin)

     Enqueue the same 10-minute fallback poll the beat schedule runs (#492's
    immediate-poll path), for every orchestration connection or just one.

    A poll only ingests runs the provider reports; it does not re-run anything, and a
    dispatch failure at the broker is a 503 with nothing enqueued and no audit row.

    Args:
        connection_id (None | Unset | UUID): Narrow to one orchestration connection

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PollNowResponse]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    connection_id: None | Unset | UUID = UNSET,
) -> HTTPValidationError | PollNowResponse | None:
    """Run the orchestration poll now, for one connection or all (admin)

     Enqueue the same 10-minute fallback poll the beat schedule runs (#492's
    immediate-poll path), for every orchestration connection or just one.

    A poll only ingests runs the provider reports; it does not re-run anything, and a
    dispatch failure at the broker is a 503 with nothing enqueued and no audit row.

    Args:
        connection_id (None | Unset | UUID): Narrow to one orchestration connection

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PollNowResponse
    """

    return sync_detailed(
        client=client,
        connection_id=connection_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    connection_id: None | Unset | UUID = UNSET,
) -> Response[HTTPValidationError | PollNowResponse]:
    """Run the orchestration poll now, for one connection or all (admin)

     Enqueue the same 10-minute fallback poll the beat schedule runs (#492's
    immediate-poll path), for every orchestration connection or just one.

    A poll only ingests runs the provider reports; it does not re-run anything, and a
    dispatch failure at the broker is a 503 with nothing enqueued and no audit row.

    Args:
        connection_id (None | Unset | UUID): Narrow to one orchestration connection

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PollNowResponse]
    """

    kwargs = _get_kwargs(
        connection_id=connection_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    connection_id: None | Unset | UUID = UNSET,
) -> HTTPValidationError | PollNowResponse | None:
    """Run the orchestration poll now, for one connection or all (admin)

     Enqueue the same 10-minute fallback poll the beat schedule runs (#492's
    immediate-poll path), for every orchestration connection or just one.

    A poll only ingests runs the provider reports; it does not re-run anything, and a
    dispatch failure at the broker is a 503 with nothing enqueued and no audit row.

    Args:
        connection_id (None | Unset | UUID): Narrow to one orchestration connection

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PollNowResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            connection_id=connection_id,
        )
    ).parsed
