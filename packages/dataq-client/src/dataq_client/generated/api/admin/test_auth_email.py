from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.auth_email_test_response import AuthEmailTestResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/auth-email/test",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AuthEmailTestResponse | None:
    if response.status_code == 200:
        response_200 = AuthEmailTestResponse.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AuthEmailTestResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AuthEmailTestResponse]:
    """SMTP pre-flight test — send a test email to the caller (ADR 0032, #737)

     Send a real test message to the CALLER's own address over the configured
    `AUTH_EMAIL_*` transport, so a misconfigured mailer is caught at install time
    rather than at a teammate's first sign-in attempt (issue #737).

    No recipient input, so it cannot relay mail. Failures: 503 (mailer not
    configured / secret store unreachable) or 502 with machine-readable
    `detail.stage` in connect/tls/auth/send. Throttled per admin (#1147,
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN`): over the cap is a real 429, and the
    charge lands BEFORE the send — a failed attempt still spends a slot.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuthEmailTestResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> AuthEmailTestResponse | None:
    """SMTP pre-flight test — send a test email to the caller (ADR 0032, #737)

     Send a real test message to the CALLER's own address over the configured
    `AUTH_EMAIL_*` transport, so a misconfigured mailer is caught at install time
    rather than at a teammate's first sign-in attempt (issue #737).

    No recipient input, so it cannot relay mail. Failures: 503 (mailer not
    configured / secret store unreachable) or 502 with machine-readable
    `detail.stage` in connect/tls/auth/send. Throttled per admin (#1147,
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN`): over the cap is a real 429, and the
    charge lands BEFORE the send — a failed attempt still spends a slot.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuthEmailTestResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[AuthEmailTestResponse]:
    """SMTP pre-flight test — send a test email to the caller (ADR 0032, #737)

     Send a real test message to the CALLER's own address over the configured
    `AUTH_EMAIL_*` transport, so a misconfigured mailer is caught at install time
    rather than at a teammate's first sign-in attempt (issue #737).

    No recipient input, so it cannot relay mail. Failures: 503 (mailer not
    configured / secret store unreachable) or 502 with machine-readable
    `detail.stage` in connect/tls/auth/send. Throttled per admin (#1147,
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN`): over the cap is a real 429, and the
    charge lands BEFORE the send — a failed attempt still spends a slot.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuthEmailTestResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> AuthEmailTestResponse | None:
    """SMTP pre-flight test — send a test email to the caller (ADR 0032, #737)

     Send a real test message to the CALLER's own address over the configured
    `AUTH_EMAIL_*` transport, so a misconfigured mailer is caught at install time
    rather than at a teammate's first sign-in attempt (issue #737).

    No recipient input, so it cannot relay mail. Failures: 503 (mailer not
    configured / secret store unreachable) or 502 with machine-readable
    `detail.stage` in connect/tls/auth/send. Throttled per admin (#1147,
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN`): over the cap is a real 429, and the
    charge lands BEFORE the send — a failed attempt still spends a slot.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuthEmailTestResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
