from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.check_read import CheckRead
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    check_id: UUID,
    version_no: int,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suites/{suite_id}/checks/{check_id}/versions/{version_no}/restore".format(
            suite_id=quote(str(suite_id), safe=""),
            check_id=quote(str(check_id), safe=""),
            version_no=quote(str(version_no), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CheckRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CheckRead.from_dict(response.json())

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
) -> Response[CheckRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    check_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[CheckRead | HTTPValidationError]:
    """Restore a check to a previous version (#283)

     Edit-gated exactly like `update_check`, because that's what this does under the hood — re-
    applies the chosen version's snapshot through the same validated PATCH path and records the
    result as a new version.

    Args:
        suite_id (UUID):
        check_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CheckRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
        version_no=version_no,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    check_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> CheckRead | HTTPValidationError | None:
    """Restore a check to a previous version (#283)

     Edit-gated exactly like `update_check`, because that's what this does under the hood — re-
    applies the chosen version's snapshot through the same validated PATCH path and records the
    result as a new version.

    Args:
        suite_id (UUID):
        check_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CheckRead | HTTPValidationError
    """

    return sync_detailed(
        suite_id=suite_id,
        check_id=check_id,
        version_no=version_no,
        client=client,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    check_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> Response[CheckRead | HTTPValidationError]:
    """Restore a check to a previous version (#283)

     Edit-gated exactly like `update_check`, because that's what this does under the hood — re-
    applies the chosen version's snapshot through the same validated PATCH path and records the
    result as a new version.

    Args:
        suite_id (UUID):
        check_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CheckRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        check_id=check_id,
        version_no=version_no,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    check_id: UUID,
    version_no: int,
    *,
    client: AuthenticatedClient | Client,
) -> CheckRead | HTTPValidationError | None:
    """Restore a check to a previous version (#283)

     Edit-gated exactly like `update_check`, because that's what this does under the hood — re-
    applies the chosen version's snapshot through the same validated PATCH path and records the
    result as a new version.

    Args:
        suite_id (UUID):
        check_id (UUID):
        version_no (int):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CheckRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            check_id=check_id,
            version_no=version_no,
            client=client,
        )
    ).parsed
