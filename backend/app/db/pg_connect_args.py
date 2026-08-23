"""Shared driver guard for psycopg-only `connect_args` keys (#1266)."""

from sqlalchemy.engine import make_url

_PSYCOPG_FAMILY_DRIVERS = frozenset({"psycopg2", "psycopg"})


def psycopg_connect_args(database_url: str, **driver_only_args: object) -> dict[str, object]:
    """Return `driver_only_args` unchanged when `database_url` resolves to a
    psycopg-family SQLAlchemy driver (`postgresql+psycopg2` or the psycopg3
    `postgresql+psycopg`), else `{}`.
    """
    try:
        is_psycopg = make_url(database_url).get_dialect().driver in _PSYCOPG_FAMILY_DRIVERS
    except Exception:
        # An unparseable URL, or a driver name SQLAlchemy has no dialect plugin for, isn't this
        # guard's story to tell — `create_engine` itself raises on it soon enough.
        is_psycopg = False
    return dict(driver_only_args) if is_psycopg else {}
