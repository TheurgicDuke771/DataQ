from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.contract_import_read import ContractImportRead
from ...models.contract_import_request import ContractImportRequest
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: ContractImportRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/contracts/import",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ContractImportRead | HTTPValidationError | None:
    if response.status_code == 201:
        response_201 = ContractImportRead.from_dict(response.json())

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
) -> Response[ContractImportRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ContractImportRequest,
) -> Response[ContractImportRead | HTTPValidationError]:
    """Import an ODCS v3 data contract

     Validates the document against the Open Data Contract Standard (v3.0 to v3.2 vendored; a newer v3
    minor is accepted with a warning; v2 and v4 are refused), stores it verbatim, and creates one suite
    per schema object on `connection_id` with the checks the translation table derives (ADR 0052). Every
    clause DataQ cannot enforce is returned in `contract.unmapped` with the reason. Member or above;
    `edit` on every suite it reconciles.

    Args:
        body (ContractImportRequest): An ODCS v3 document as JSON (`document`) or YAML text
            (`document_yaml`) — exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContractImportRead | HTTPValidationError]
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
    body: ContractImportRequest,
) -> ContractImportRead | HTTPValidationError | None:
    """Import an ODCS v3 data contract

     Validates the document against the Open Data Contract Standard (v3.0 to v3.2 vendored; a newer v3
    minor is accepted with a warning; v2 and v4 are refused), stores it verbatim, and creates one suite
    per schema object on `connection_id` with the checks the translation table derives (ADR 0052). Every
    clause DataQ cannot enforce is returned in `contract.unmapped` with the reason. Member or above;
    `edit` on every suite it reconciles.

    Args:
        body (ContractImportRequest): An ODCS v3 document as JSON (`document`) or YAML text
            (`document_yaml`) — exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContractImportRead | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ContractImportRequest,
) -> Response[ContractImportRead | HTTPValidationError]:
    """Import an ODCS v3 data contract

     Validates the document against the Open Data Contract Standard (v3.0 to v3.2 vendored; a newer v3
    minor is accepted with a warning; v2 and v4 are refused), stores it verbatim, and creates one suite
    per schema object on `connection_id` with the checks the translation table derives (ADR 0052). Every
    clause DataQ cannot enforce is returned in `contract.unmapped` with the reason. Member or above;
    `edit` on every suite it reconciles.

    Args:
        body (ContractImportRequest): An ODCS v3 document as JSON (`document`) or YAML text
            (`document_yaml`) — exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContractImportRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ContractImportRequest,
) -> ContractImportRead | HTTPValidationError | None:
    """Import an ODCS v3 data contract

     Validates the document against the Open Data Contract Standard (v3.0 to v3.2 vendored; a newer v3
    minor is accepted with a warning; v2 and v4 are refused), stores it verbatim, and creates one suite
    per schema object on `connection_id` with the checks the translation table derives (ADR 0052). Every
    clause DataQ cannot enforce is returned in `contract.unmapped` with the reason. Member or above;
    `edit` on every suite it reconciles.

    Args:
        body (ContractImportRequest): An ODCS v3 document as JSON (`document`) or YAML text
            (`document_yaml`) — exactly one.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContractImportRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
