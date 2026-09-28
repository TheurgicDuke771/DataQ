from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.dashboard_summary_read import DashboardSummaryRead
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    window_days: int | Unset = 7,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["window_days"] = window_days

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/dashboard/summary",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DashboardSummaryRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = DashboardSummaryRead.from_dict(response.json())

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
) -> Response[DashboardSummaryRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    window_days: int | Unset = 7,
) -> Response[DashboardSummaryRead | HTTPValidationError]:
    """Dashboard summary — KPIs, run trend, per-suite performance

    Args:
        window_days (int | Unset):  Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DashboardSummaryRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        window_days=window_days,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    window_days: int | Unset = 7,
) -> DashboardSummaryRead | HTTPValidationError | None:
    """Dashboard summary — KPIs, run trend, per-suite performance

    Args:
        window_days (int | Unset):  Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DashboardSummaryRead | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        window_days=window_days,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    window_days: int | Unset = 7,
) -> Response[DashboardSummaryRead | HTTPValidationError]:
    """Dashboard summary — KPIs, run trend, per-suite performance

    Args:
        window_days (int | Unset):  Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DashboardSummaryRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        window_days=window_days,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    window_days: int | Unset = 7,
) -> DashboardSummaryRead | HTTPValidationError | None:
    """Dashboard summary — KPIs, run trend, per-suite performance

    Args:
        window_days (int | Unset):  Default: 7.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DashboardSummaryRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            window_days=window_days,
        )
    ).parsed
