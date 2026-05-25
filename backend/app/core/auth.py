"""Azure AD bearer-token auth + user upsert.

Two operating modes — picked once at import time from settings:

- **Real mode** — `AZURE_TENANT_ID` + `AZURE_API_CLIENT_ID` are set.
  Tokens are validated by `fastapi-azure-auth`: issuer, audience, signature,
  expiry, and the configured scope. The OpenID config is loaded at app
  startup via `init_auth()` and refreshed automatically.

- **Dev bypass** — all three of:
  `ENVIRONMENT=dev`, `AUTH_DEV_BYPASS=true`, Azure vars empty.
  No token required. Resolves every request to a fixed dev user upserted
  into the `users` table. Intended for local development against a
  Postgres in `docker-compose` without a real Azure tenant.

If neither mode is configured, `init_auth` raises at startup — fail-closed.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Security
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from fastapi_azure_auth.user import User as AzureUser
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import User
from backend.app.db.session import get_db

log = get_logger(__name__)

DEV_BYPASS_AAD_OID = "00000000-0000-0000-0000-000000000001"
DEV_BYPASS_EMAIL = "dev-bypass@dataq.local"
DEV_BYPASS_DISPLAY_NAME = "Dev Bypass User"


def _dev_bypass_allowed(settings: Settings) -> bool:
    return (
        settings.environment == "dev"
        and settings.auth_dev_bypass
        and not settings.azure_auth_configured
    )


def _build_azure_scheme(
    settings: Settings,
) -> SingleTenantAzureAuthorizationCodeBearer | None:
    if not settings.azure_auth_configured:
        return None
    assert settings.azure_api_client_id is not None
    assert settings.azure_tenant_id is not None
    assert settings.azure_api_scope_uri is not None
    return SingleTenantAzureAuthorizationCodeBearer(
        app_client_id=settings.azure_api_client_id,
        tenant_id=settings.azure_tenant_id,
        scopes={settings.azure_api_scope_uri: settings.azure_api_scope},
    )


_settings = get_settings()
azure_scheme: SingleTenantAzureAuthorizationCodeBearer | None = _build_azure_scheme(_settings)


def _upsert_user(
    db: Session,
    *,
    aad_object_id: str,
    email: str,
    display_name: str | None,
) -> User:
    now = datetime.now(UTC)
    stmt = (
        insert(User)
        .values(
            aad_object_id=aad_object_id,
            email=email,
            display_name=display_name,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["aad_object_id"],
            set_={
                "email": email,
                "display_name": display_name,
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        .returning(User)
    )
    user = db.execute(stmt).scalar_one()
    db.commit()
    return user


def _extract_claims(azure_user: AzureUser) -> tuple[str, str, str | None]:
    claims: dict[str, Any] = azure_user.claims
    aad_oid = str(claims["oid"])
    email = str(claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "")
    display_name_raw = claims.get("name")
    display_name = str(display_name_raw) if display_name_raw is not None else None
    return aad_oid, email, display_name


async def init_auth() -> None:
    """Wire app startup: load OIDC config in real mode, or fail-closed.

    Called from the FastAPI lifespan.
    """
    if azure_scheme is not None:
        await azure_scheme.openid_config.load_config()
        log.info(
            "auth_real_mode_ready",
            tenant_id=_settings.azure_tenant_id,
            client_id=_settings.azure_api_client_id,
            scope=_settings.azure_api_scope_uri,
        )
        return
    if _dev_bypass_allowed(_settings):
        log.warning(
            "auth_dev_bypass_active",
            environment=_settings.environment,
            note=(
                "Every request resolves to a fixed dev user. "
                "Do NOT run with this configuration outside local dev."
            ),
        )
        return
    raise RuntimeError(
        "Auth not configured. Set AZURE_TENANT_ID + AZURE_API_CLIENT_ID, "
        "or set ENVIRONMENT=dev with AUTH_DEV_BYPASS=true for local dev."
    )


def _get_current_user_real(
    azure_user: Annotated[AzureUser, Security(azure_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    aad_oid, email, display_name = _extract_claims(azure_user)
    user = _upsert_user(db, aad_object_id=aad_oid, email=email, display_name=display_name)
    log.info("auth_user_resolved", mode="real", aad_oid=aad_oid, user_id=str(user.id))
    return user


def _get_current_user_dev_bypass(
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = _upsert_user(
        db,
        aad_object_id=DEV_BYPASS_AAD_OID,
        email=DEV_BYPASS_EMAIL,
        display_name=DEV_BYPASS_DISPLAY_NAME,
    )
    log.debug("auth_user_resolved", mode="dev_bypass", user_id=str(user.id))
    return user


def _get_current_user_unconfigured() -> User:
    # init_auth will have raised at startup; this is defence-in-depth.
    raise DataQError(
        code="auth_not_configured",
        message="Authentication is not configured for this environment.",
        status_code=503,
    )


get_current_user: Callable[..., User]
if azure_scheme is not None:
    get_current_user = _get_current_user_real
elif _dev_bypass_allowed(_settings):
    get_current_user = _get_current_user_dev_bypass
else:
    get_current_user = _get_current_user_unconfigured
