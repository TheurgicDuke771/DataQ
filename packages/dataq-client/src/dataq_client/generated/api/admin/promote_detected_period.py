from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.seasonal_period_promote import SeasonalPeriodPromote
from ...models.seasonal_period_read import SeasonalPeriodRead
from ...types import Response


def _get_kwargs(
    *,
    body: SeasonalPeriodPromote,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/seasonal-periods/promote",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SeasonalPeriodRead | None:
    if response.status_code == 201:
        response_201 = SeasonalPeriodRead.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | SeasonalPeriodRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SeasonalPeriodPromote,
) -> Response[HTTPValidationError | SeasonalPeriodRead]:
    """Add a detected period to the calendar (admin)

    Args:
        body (SeasonalPeriodPromote): Promote a period detected in a check's history
            (`GET /suites/{suite_id}/checks/{check_id}/seasonal-periods/detected`) to the calendar.
            `start` is the detected period's `start` (its most recent season); the server reads the
            history again and refuses a start it no longer shows (404), or a season an entry on the
            calendar already covers (409, naming the entry to attach instead). A recurring period
            becomes a
            month/day range, a period seen once a dated entry (`series` applies only to that).
            `attach` also attaches the new entry to the check, which needs `yearly` on it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SeasonalPeriodRead]
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
    body: SeasonalPeriodPromote,
) -> HTTPValidationError | SeasonalPeriodRead | None:
    """Add a detected period to the calendar (admin)

    Args:
        body (SeasonalPeriodPromote): Promote a period detected in a check's history
            (`GET /suites/{suite_id}/checks/{check_id}/seasonal-periods/detected`) to the calendar.
            `start` is the detected period's `start` (its most recent season); the server reads the
            history again and refuses a start it no longer shows (404), or a season an entry on the
            calendar already covers (409, naming the entry to attach instead). A recurring period
            becomes a
            month/day range, a period seen once a dated entry (`series` applies only to that).
            `attach` also attaches the new entry to the check, which needs `yearly` on it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SeasonalPeriodRead
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: SeasonalPeriodPromote,
) -> Response[HTTPValidationError | SeasonalPeriodRead]:
    """Add a detected period to the calendar (admin)

    Args:
        body (SeasonalPeriodPromote): Promote a period detected in a check's history
            (`GET /suites/{suite_id}/checks/{check_id}/seasonal-periods/detected`) to the calendar.
            `start` is the detected period's `start` (its most recent season); the server reads the
            history again and refuses a start it no longer shows (404), or a season an entry on the
            calendar already covers (409, naming the entry to attach instead). A recurring period
            becomes a
            month/day range, a period seen once a dated entry (`series` applies only to that).
            `attach` also attaches the new entry to the check, which needs `yearly` on it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SeasonalPeriodRead]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: SeasonalPeriodPromote,
) -> HTTPValidationError | SeasonalPeriodRead | None:
    """Add a detected period to the calendar (admin)

    Args:
        body (SeasonalPeriodPromote): Promote a period detected in a check's history
            (`GET /suites/{suite_id}/checks/{check_id}/seasonal-periods/detected`) to the calendar.
            `start` is the detected period's `start` (its most recent season); the server reads the
            history again and refuses a start it no longer shows (404), or a season an entry on the
            calendar already covers (409, naming the entry to attach instead). A recurring period
            becomes a
            month/day range, a period seen once a dated entry (`series` applies only to that).
            `attach` also attaches the new entry to the check, which needs `yearly` on it.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SeasonalPeriodRead
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
