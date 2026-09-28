from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.secret_sweep_run_response import SecretSweepRunResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/secret-sweep/run",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> SecretSweepRunResponse | None:
    if response.status_code == 202:
        response_202 = SecretSweepRunResponse.from_dict(response.json())

        return response_202

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[SecretSweepRunResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SecretSweepRunResponse]:
    """Run the orphan-secret sweep now — report-only (admin)

     Enqueues the existing beat task in report-only mode, REGARDLESS of
    `SECRET_ORPHAN_PURGE` — a UI-triggered run must never purge a live warehouse
    credential. Returns immediately with the Celery task id; the result lands in
    `GET /admin/secret-sweep` once the worker picks it up.
    503 with a classified broker reason when the broker is down; nothing enqueued, no audit row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SecretSweepRunResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> SecretSweepRunResponse | None:
    """Run the orphan-secret sweep now — report-only (admin)

     Enqueues the existing beat task in report-only mode, REGARDLESS of
    `SECRET_ORPHAN_PURGE` — a UI-triggered run must never purge a live warehouse
    credential. Returns immediately with the Celery task id; the result lands in
    `GET /admin/secret-sweep` once the worker picks it up.
    503 with a classified broker reason when the broker is down; nothing enqueued, no audit row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SecretSweepRunResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[SecretSweepRunResponse]:
    """Run the orphan-secret sweep now — report-only (admin)

     Enqueues the existing beat task in report-only mode, REGARDLESS of
    `SECRET_ORPHAN_PURGE` — a UI-triggered run must never purge a live warehouse
    credential. Returns immediately with the Celery task id; the result lands in
    `GET /admin/secret-sweep` once the worker picks it up.
    503 with a classified broker reason when the broker is down; nothing enqueued, no audit row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[SecretSweepRunResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> SecretSweepRunResponse | None:
    """Run the orphan-secret sweep now — report-only (admin)

     Enqueues the existing beat task in report-only mode, REGARDLESS of
    `SECRET_ORPHAN_PURGE` — a UI-triggered run must never purge a live warehouse
    credential. Returns immediately with the Celery task id; the result lands in
    `GET /admin/secret-sweep` once the worker picks it up.
    503 with a classified broker reason when the broker is down; nothing enqueued, no audit row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        SecretSweepRunResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
