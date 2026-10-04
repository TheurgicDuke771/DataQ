from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.scorecard_read import ScorecardRead
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/dashboard/dimensions",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ScorecardRead | None:
    if response.status_code == 200:
        response_200 = ScorecardRead.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ScorecardRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[ScorecardRead]:
    """Each DQ dimension across every suite in the workspace

     One row per DQ dimension that has checks anywhere; `uncovered` lists the
    dimensions no suite has a check for. Each suite is scored from its latest run, and
    only if that run completed: a suite that is mid-run, failed or was cancelled keeps
    its checks in `checks_total` but adds nothing to the score until its next completed
    run. An earlier completed run is not used in its place.

    **Workspace-wide, unlike `/dashboard/summary`**: it covers every suite, including
    ones the caller cannot open, and is identical for every member. Checks with no
    dimension are counted in `unclassified_checks` and are in no row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ScorecardRead]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> ScorecardRead | None:
    """Each DQ dimension across every suite in the workspace

     One row per DQ dimension that has checks anywhere; `uncovered` lists the
    dimensions no suite has a check for. Each suite is scored from its latest run, and
    only if that run completed: a suite that is mid-run, failed or was cancelled keeps
    its checks in `checks_total` but adds nothing to the score until its next completed
    run. An earlier completed run is not used in its place.

    **Workspace-wide, unlike `/dashboard/summary`**: it covers every suite, including
    ones the caller cannot open, and is identical for every member. Checks with no
    dimension are counted in `unclassified_checks` and are in no row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ScorecardRead
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[ScorecardRead]:
    """Each DQ dimension across every suite in the workspace

     One row per DQ dimension that has checks anywhere; `uncovered` lists the
    dimensions no suite has a check for. Each suite is scored from its latest run, and
    only if that run completed: a suite that is mid-run, failed or was cancelled keeps
    its checks in `checks_total` but adds nothing to the score until its next completed
    run. An earlier completed run is not used in its place.

    **Workspace-wide, unlike `/dashboard/summary`**: it covers every suite, including
    ones the caller cannot open, and is identical for every member. Checks with no
    dimension are counted in `unclassified_checks` and are in no row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ScorecardRead]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> ScorecardRead | None:
    """Each DQ dimension across every suite in the workspace

     One row per DQ dimension that has checks anywhere; `uncovered` lists the
    dimensions no suite has a check for. Each suite is scored from its latest run, and
    only if that run completed: a suite that is mid-run, failed or was cancelled keeps
    its checks in `checks_total` but adds nothing to the score until its next completed
    run. An earlier completed run is not used in its place.

    **Workspace-wide, unlike `/dashboard/summary`**: it covers every suite, including
    ones the caller cannot open, and is identical for every member. Checks with no
    dimension are counted in `unclassified_checks` and are in no row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ScorecardRead
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
