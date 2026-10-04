from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.asset_summary_read import AssetSummaryRead
from ...models.http_validation_error import HTTPValidationError
from ...models.list_assets_sort import ListAssetsSort
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    limit: int | Unset = 200,
    offset: int | Unset = 0,
    sort: ListAssetsSort | Unset = ListAssetsSort.NAME,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params["offset"] = offset

    json_sort: str | Unset = UNSET
    if not isinstance(sort, Unset):
        json_sort = sort.value

    params["sort"] = json_sort

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assets",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | list[AssetSummaryRead] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = AssetSummaryRead.from_dict(response_200_item_data)

            response_200.append(response_200_item)

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
) -> Response[HTTPValidationError | list[AssetSummaryRead]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 200,
    offset: int | Unset = 0,
    sort: ListAssetsSort | Unset = ListAssetsSort.NAME,
) -> Response[HTTPValidationError | list[AssetSummaryRead]]:
    """List assets

    Args:
        limit (int | Unset):  Default: 200.
        offset (int | Unset):  Default: 0.
        sort (ListAssetsSort | Unset): `name` orders by namespace then name. `health_score` orders
            the whole population lowest score first, assets with no score last. Default:
            ListAssetsSort.NAME.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[AssetSummaryRead]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        offset=offset,
        sort=sort,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 200,
    offset: int | Unset = 0,
    sort: ListAssetsSort | Unset = ListAssetsSort.NAME,
) -> HTTPValidationError | list[AssetSummaryRead] | None:
    """List assets

    Args:
        limit (int | Unset):  Default: 200.
        offset (int | Unset):  Default: 0.
        sort (ListAssetsSort | Unset): `name` orders by namespace then name. `health_score` orders
            the whole population lowest score first, assets with no score last. Default:
            ListAssetsSort.NAME.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[AssetSummaryRead]
    """

    return sync_detailed(
        client=client,
        limit=limit,
        offset=offset,
        sort=sort,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 200,
    offset: int | Unset = 0,
    sort: ListAssetsSort | Unset = ListAssetsSort.NAME,
) -> Response[HTTPValidationError | list[AssetSummaryRead]]:
    """List assets

    Args:
        limit (int | Unset):  Default: 200.
        offset (int | Unset):  Default: 0.
        sort (ListAssetsSort | Unset): `name` orders by namespace then name. `health_score` orders
            the whole population lowest score first, assets with no score last. Default:
            ListAssetsSort.NAME.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | list[AssetSummaryRead]]
    """

    kwargs = _get_kwargs(
        limit=limit,
        offset=offset,
        sort=sort,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 200,
    offset: int | Unset = 0,
    sort: ListAssetsSort | Unset = ListAssetsSort.NAME,
) -> HTTPValidationError | list[AssetSummaryRead] | None:
    """List assets

    Args:
        limit (int | Unset):  Default: 200.
        offset (int | Unset):  Default: 0.
        sort (ListAssetsSort | Unset): `name` orders by namespace then name. `health_score` orders
            the whole population lowest score first, assets with no score last. Default:
            ListAssetsSort.NAME.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | list[AssetSummaryRead]
    """

    return (
        await asyncio_detailed(
            client=client,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    ).parsed
