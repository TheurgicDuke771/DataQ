import datetime
from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.audit_event_page import AuditEventPage
from ...models.http_validation_error import HTTPValidationError
from ...models.list_audit_events_action_class_type_0 import ListAuditEventsActionClassType0
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    action_class: ListAuditEventsActionClassType0 | None | Unset = UNSET,
    entity_type: None | str | Unset = UNSET,
    entity_id: None | Unset | UUID = UNSET,
    actor_user_id: None | Unset | UUID = UNSET,
    action: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_action_class: None | str | Unset
    if isinstance(action_class, Unset):
        json_action_class = UNSET
    elif isinstance(action_class, ListAuditEventsActionClassType0):
        json_action_class = action_class.value
    else:
        json_action_class = action_class
    params["action_class"] = json_action_class

    json_entity_type: None | str | Unset
    if isinstance(entity_type, Unset):
        json_entity_type = UNSET
    else:
        json_entity_type = entity_type
    params["entity_type"] = json_entity_type

    json_entity_id: None | str | Unset
    if isinstance(entity_id, Unset):
        json_entity_id = UNSET
    elif isinstance(entity_id, UUID):
        json_entity_id = str(entity_id)
    else:
        json_entity_id = entity_id
    params["entity_id"] = json_entity_id

    json_actor_user_id: None | str | Unset
    if isinstance(actor_user_id, Unset):
        json_actor_user_id = UNSET
    elif isinstance(actor_user_id, UUID):
        json_actor_user_id = str(actor_user_id)
    else:
        json_actor_user_id = actor_user_id
    params["actor_user_id"] = json_actor_user_id

    json_action: None | str | Unset
    if isinstance(action, Unset):
        json_action = UNSET
    else:
        json_action = action
    params["action"] = json_action

    json_since: None | str | Unset
    if isinstance(since, Unset):
        json_since = UNSET
    elif isinstance(since, datetime.datetime):
        json_since = since.isoformat()
    else:
        json_since = since
    params["since"] = json_since

    json_until: None | str | Unset
    if isinstance(until, Unset):
        json_until = UNSET
    elif isinstance(until, datetime.datetime):
        json_until = until.isoformat()
    else:
        json_until = until
    params["until"] = json_until

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/admin/audit-events",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AuditEventPage | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AuditEventPage.from_dict(response.json())

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
) -> Response[AuditEventPage | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    action_class: ListAuditEventsActionClassType0 | None | Unset = UNSET,
    entity_type: None | str | Unset = UNSET,
    entity_id: None | Unset | UUID = UNSET,
    actor_user_id: None | Unset | UUID = UNSET,
    action: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[AuditEventPage | HTTPValidationError]:
    """Query the append-only audit log (workspace-admin only)

     The durable record of deliberate acts by a principal, newest first.

    Args:
        action_class (ListAuditEventsActionClassType0 | None | Unset):
        entity_type (None | str | Unset):
        entity_id (None | Unset | UUID):
        actor_user_id (None | Unset | UUID):
        action (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditEventPage | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        action_class=action_class,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    action_class: ListAuditEventsActionClassType0 | None | Unset = UNSET,
    entity_type: None | str | Unset = UNSET,
    entity_id: None | Unset | UUID = UNSET,
    actor_user_id: None | Unset | UUID = UNSET,
    action: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> AuditEventPage | HTTPValidationError | None:
    """Query the append-only audit log (workspace-admin only)

     The durable record of deliberate acts by a principal, newest first.

    Args:
        action_class (ListAuditEventsActionClassType0 | None | Unset):
        entity_type (None | str | Unset):
        entity_id (None | Unset | UUID):
        actor_user_id (None | Unset | UUID):
        action (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditEventPage | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        action_class=action_class,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    action_class: ListAuditEventsActionClassType0 | None | Unset = UNSET,
    entity_type: None | str | Unset = UNSET,
    entity_id: None | Unset | UUID = UNSET,
    actor_user_id: None | Unset | UUID = UNSET,
    action: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[AuditEventPage | HTTPValidationError]:
    """Query the append-only audit log (workspace-admin only)

     The durable record of deliberate acts by a principal, newest first.

    Args:
        action_class (ListAuditEventsActionClassType0 | None | Unset):
        entity_type (None | str | Unset):
        entity_id (None | Unset | UUID):
        actor_user_id (None | Unset | UUID):
        action (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuditEventPage | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        action_class=action_class,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    action_class: ListAuditEventsActionClassType0 | None | Unset = UNSET,
    entity_type: None | str | Unset = UNSET,
    entity_id: None | Unset | UUID = UNSET,
    actor_user_id: None | Unset | UUID = UNSET,
    action: None | str | Unset = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> AuditEventPage | HTTPValidationError | None:
    """Query the append-only audit log (workspace-admin only)

     The durable record of deliberate acts by a principal, newest first.

    Args:
        action_class (ListAuditEventsActionClassType0 | None | Unset):
        entity_type (None | str | Unset):
        entity_id (None | Unset | UUID):
        actor_user_id (None | Unset | UUID):
        action (None | str | Unset):
        since (datetime.datetime | None | Unset):
        until (datetime.datetime | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuditEventPage | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            action_class=action_class,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            action=action,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    ).parsed
