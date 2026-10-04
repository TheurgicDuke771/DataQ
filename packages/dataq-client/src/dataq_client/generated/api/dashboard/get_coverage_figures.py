from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.coverage_figures_read import CoverageFiguresRead
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    false_positive_window_days: int | Unset = 30,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["false_positive_window_days"] = false_positive_window_days

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/dashboard/coverage",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CoverageFiguresRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CoverageFiguresRead.from_dict(response.json())

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
) -> Response[CoverageFiguresRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    false_positive_window_days: int | Unset = 30,
) -> Response[CoverageFiguresRead | HTTPValidationError]:
    """Coverage of the asset inventory and the false-positive rate of automatic checks

    Args:
        false_positive_window_days (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CoverageFiguresRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        false_positive_window_days=false_positive_window_days,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    false_positive_window_days: int | Unset = 30,
) -> CoverageFiguresRead | HTTPValidationError | None:
    """Coverage of the asset inventory and the false-positive rate of automatic checks

    Args:
        false_positive_window_days (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CoverageFiguresRead | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        false_positive_window_days=false_positive_window_days,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    false_positive_window_days: int | Unset = 30,
) -> Response[CoverageFiguresRead | HTTPValidationError]:
    """Coverage of the asset inventory and the false-positive rate of automatic checks

    Args:
        false_positive_window_days (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CoverageFiguresRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        false_positive_window_days=false_positive_window_days,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    false_positive_window_days: int | Unset = 30,
) -> CoverageFiguresRead | HTTPValidationError | None:
    """Coverage of the asset inventory and the false-positive rate of automatic checks

    Args:
        false_positive_window_days (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CoverageFiguresRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            false_positive_window_days=false_positive_window_days,
        )
    ).parsed
