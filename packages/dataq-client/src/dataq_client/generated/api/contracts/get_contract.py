from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.contract_read import ContractRead
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    contract_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/contracts/{contract_id}".format(
            contract_id=quote(str(contract_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ContractRead | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ContractRead.from_dict(response.json())

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
) -> Response[ContractRead | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    contract_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ContractRead | HTTPValidationError]:
    """Get a data contract

    Args:
        contract_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContractRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        contract_id=contract_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    contract_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ContractRead | HTTPValidationError | None:
    """Get a data contract

    Args:
        contract_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContractRead | HTTPValidationError
    """

    return sync_detailed(
        contract_id=contract_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    contract_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ContractRead | HTTPValidationError]:
    """Get a data contract

    Args:
        contract_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContractRead | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        contract_id=contract_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    contract_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ContractRead | HTTPValidationError | None:
    """Get a data contract

    Args:
        contract_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContractRead | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            contract_id=contract_id,
            client=client,
        )
    ).parsed
