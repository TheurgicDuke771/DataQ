from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.suite_apply_read import SuiteApplyRead
from ...models.suite_apply_request import SuiteApplyRequest
from ...types import Response


def _get_kwargs(
    suite_id: UUID,
    *,
    body: SuiteApplyRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/suites/{suite_id}/apply".format(
            suite_id=quote(str(suite_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SuiteApplyRead | None:
    if response.status_code == 200:
        response_200 = SuiteApplyRead.from_dict(response.json())

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
) -> Response[HTTPValidationError | SuiteApplyRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteApplyRequest,
) -> Response[HTTPValidationError | SuiteApplyRead]:
    """Apply a suite document onto an existing suite, or report its drift

     Idempotent: applying the same document twice changes nothing the second time.

    Checks are matched by **name**. A check the document names and the suite lacks is
    created; one that differs is updated; one the suite has and the document does not
    is left alone unless `prune` is true, when it is deleted along with its results and
    history. The suite's connection and target are never touched.

    The whole document is validated before anything is written. Each change is then
    made the way an edit in the app is, with its own version and audit event — so a
    failure part-way (a concurrent edit, say) leaves the earlier changes in place;
    apply again to finish.

    Args:
        suite_id (UUID):
        body (SuiteApplyRequest): A suite document to apply onto THIS suite — JSON (`document`) or
            YAML text
            (`document_yaml`), exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteApplyRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteApplyRequest,
) -> HTTPValidationError | SuiteApplyRead | None:
    """Apply a suite document onto an existing suite, or report its drift

     Idempotent: applying the same document twice changes nothing the second time.

    Checks are matched by **name**. A check the document names and the suite lacks is
    created; one that differs is updated; one the suite has and the document does not
    is left alone unless `prune` is true, when it is deleted along with its results and
    history. The suite's connection and target are never touched.

    The whole document is validated before anything is written. Each change is then
    made the way an edit in the app is, with its own version and audit event — so a
    failure part-way (a concurrent edit, say) leaves the earlier changes in place;
    apply again to finish.

    Args:
        suite_id (UUID):
        body (SuiteApplyRequest): A suite document to apply onto THIS suite — JSON (`document`) or
            YAML text
            (`document_yaml`), exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteApplyRead
    """

    return sync_detailed(
        suite_id=suite_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteApplyRequest,
) -> Response[HTTPValidationError | SuiteApplyRead]:
    """Apply a suite document onto an existing suite, or report its drift

     Idempotent: applying the same document twice changes nothing the second time.

    Checks are matched by **name**. A check the document names and the suite lacks is
    created; one that differs is updated; one the suite has and the document does not
    is left alone unless `prune` is true, when it is deleted along with its results and
    history. The suite's connection and target are never touched.

    The whole document is validated before anything is written. Each change is then
    made the way an edit in the app is, with its own version and audit event — so a
    failure part-way (a concurrent edit, say) leaves the earlier changes in place;
    apply again to finish.

    Args:
        suite_id (UUID):
        body (SuiteApplyRequest): A suite document to apply onto THIS suite — JSON (`document`) or
            YAML text
            (`document_yaml`), exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SuiteApplyRead]
    """

    kwargs = _get_kwargs(
        suite_id=suite_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    suite_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SuiteApplyRequest,
) -> HTTPValidationError | SuiteApplyRead | None:
    """Apply a suite document onto an existing suite, or report its drift

     Idempotent: applying the same document twice changes nothing the second time.

    Checks are matched by **name**. A check the document names and the suite lacks is
    created; one that differs is updated; one the suite has and the document does not
    is left alone unless `prune` is true, when it is deleted along with its results and
    history. The suite's connection and target are never touched.

    The whole document is validated before anything is written. Each change is then
    made the way an edit in the app is, with its own version and audit event — so a
    failure part-way (a concurrent edit, say) leaves the earlier changes in place;
    apply again to finish.

    Args:
        suite_id (UUID):
        body (SuiteApplyRequest): A suite document to apply onto THIS suite — JSON (`document`) or
            YAML text
            (`document_yaml`), exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SuiteApplyRead
    """

    return (
        await asyncio_detailed(
            suite_id=suite_id,
            client=client,
            body=body,
        )
    ).parsed
