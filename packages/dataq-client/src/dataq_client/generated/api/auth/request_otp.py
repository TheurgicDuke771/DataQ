from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.otp_request import OtpRequest
from ...models.otp_request_ack import OtpRequestAck
from ...types import Response


def _get_kwargs(
    *,
    body: OtpRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/auth/otp/request",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OtpRequestAck | None:
    if response.status_code == 200:
        response_200 = OtpRequestAck.from_dict(response.json())

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
) -> Response[ErrorResponse | OtpRequestAck]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: OtpRequest,
) -> Response[ErrorResponse | OtpRequestAck]:
    """Request an email sign-in code

     Email a one-time sign-in code, if the address is eligible.

    The response is IDENTICAL whether or not it is — ineligible, not allow-listed,
    or over quota all answer `{"status": "ok"}` after the same minimum elapsed time
    (anti-enumeration, ADR 0032 decision 4). The mail itself is sent by the worker
    (#1731): nothing on this path waits on SMTP or the secret store, so a slow relay
    cannot push an eligible address past the floor — and a delivery failure is an
    operator log line, never a different response.

    Args:
        body (OtpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OtpRequestAck]
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
    body: OtpRequest,
) -> ErrorResponse | OtpRequestAck | None:
    """Request an email sign-in code

     Email a one-time sign-in code, if the address is eligible.

    The response is IDENTICAL whether or not it is — ineligible, not allow-listed,
    or over quota all answer `{"status": "ok"}` after the same minimum elapsed time
    (anti-enumeration, ADR 0032 decision 4). The mail itself is sent by the worker
    (#1731): nothing on this path waits on SMTP or the secret store, so a slow relay
    cannot push an eligible address past the floor — and a delivery failure is an
    operator log line, never a different response.

    Args:
        body (OtpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OtpRequestAck
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: OtpRequest,
) -> Response[ErrorResponse | OtpRequestAck]:
    """Request an email sign-in code

     Email a one-time sign-in code, if the address is eligible.

    The response is IDENTICAL whether or not it is — ineligible, not allow-listed,
    or over quota all answer `{"status": "ok"}` after the same minimum elapsed time
    (anti-enumeration, ADR 0032 decision 4). The mail itself is sent by the worker
    (#1731): nothing on this path waits on SMTP or the secret store, so a slow relay
    cannot push an eligible address past the floor — and a delivery failure is an
    operator log line, never a different response.

    Args:
        body (OtpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OtpRequestAck]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: OtpRequest,
) -> ErrorResponse | OtpRequestAck | None:
    """Request an email sign-in code

     Email a one-time sign-in code, if the address is eligible.

    The response is IDENTICAL whether or not it is — ineligible, not allow-listed,
    or over quota all answer `{"status": "ok"}` after the same minimum elapsed time
    (anti-enumeration, ADR 0032 decision 4). The mail itself is sent by the worker
    (#1731): nothing on this path waits on SMTP or the secret store, so a slow relay
    cannot push an eligible address past the floor — and a delivery failure is an
    operator log line, never a different response.

    Args:
        body (OtpRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OtpRequestAck
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
