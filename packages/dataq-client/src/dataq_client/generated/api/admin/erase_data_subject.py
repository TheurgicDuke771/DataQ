from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.data_subject_erasure_response import DataSubjectErasureResponse
from ...models.data_subject_request import DataSubjectRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: DataSubjectRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/admin/data-subject-requests/erase",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DataSubjectErasureResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = DataSubjectErasureResponse.from_dict(response.json())

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
) -> Response[DataSubjectErasureResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> Response[DataSubjectErasureResponse | HTTPValidationError]:
    """Erase a data subject's captured sample data (admin only, GDPR Art 17 / CCPA delete)

     Workspace-wide, on-demand erasure of every captured sample cell naming
    `column` = `value` — surgical (only the matching row/cell), not a blanket purge;
    see `data_subject_requests.erase_matching_results`. Runs synchronously and
    records one `audit_events` row inside the same transaction as the scrub, so a
    failed write leaves nothing behind and an applied one cannot go unrecorded
    (mirrors the ADR 0041 phase-1 mutation pattern).

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DataSubjectErasureResponse | HTTPValidationError]
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
    body: DataSubjectRequest,
) -> DataSubjectErasureResponse | HTTPValidationError | None:
    """Erase a data subject's captured sample data (admin only, GDPR Art 17 / CCPA delete)

     Workspace-wide, on-demand erasure of every captured sample cell naming
    `column` = `value` — surgical (only the matching row/cell), not a blanket purge;
    see `data_subject_requests.erase_matching_results`. Runs synchronously and
    records one `audit_events` row inside the same transaction as the scrub, so a
    failed write leaves nothing behind and an applied one cannot go unrecorded
    (mirrors the ADR 0041 phase-1 mutation pattern).

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DataSubjectErasureResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> Response[DataSubjectErasureResponse | HTTPValidationError]:
    """Erase a data subject's captured sample data (admin only, GDPR Art 17 / CCPA delete)

     Workspace-wide, on-demand erasure of every captured sample cell naming
    `column` = `value` — surgical (only the matching row/cell), not a blanket purge;
    see `data_subject_requests.erase_matching_results`. Runs synchronously and
    records one `audit_events` row inside the same transaction as the scrub, so a
    failed write leaves nothing behind and an applied one cannot go unrecorded
    (mirrors the ADR 0041 phase-1 mutation pattern).

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DataSubjectErasureResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: DataSubjectRequest,
) -> DataSubjectErasureResponse | HTTPValidationError | None:
    """Erase a data subject's captured sample data (admin only, GDPR Art 17 / CCPA delete)

     Workspace-wide, on-demand erasure of every captured sample cell naming
    `column` = `value` — surgical (only the matching row/cell), not a blanket purge;
    see `data_subject_requests.erase_matching_results`. Runs synchronously and
    records one `audit_events` row inside the same transaction as the scrub, so a
    failed write leaves nothing behind and an applied one cannot go unrecorded
    (mirrors the ADR 0041 phase-1 mutation pattern).

    Args:
        body (DataSubjectRequest): The (column, value) pair identifying a subject's warehouse row
            — DataQ has
            no people-table, so this IS the subject identifier.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DataSubjectErasureResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
