"""One-off cleanup for #2112: clear the false "DMF unavailable" the old probe stored.

    python -m backend.scripts.clear_misprobed_dmf_capability           # dry run: list only
    python -m backend.scripts.clear_misprobed_dmf_capability --apply   # write

Touches only Snowflake connections whose stored `engine_capabilities.dmf` carries the
old probe's FRESHNESS column-type reason; they then read "not yet tested" until the next
connection test re-probes them. Idempotent. Never contacts Snowflake.
"""

from __future__ import annotations

import argparse

from backend.app.db.session import get_session
from backend.app.services.connection_service import clear_misprobed_dmf_capabilities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    session = get_session()
    try:
        affected = clear_misprobed_dmf_capabilities(session, apply=args.apply)
    finally:
        session.close()
    verb = "cleared" if args.apply else "would clear"
    print(f"{verb} the misprobed DMF capability on {len(affected)} connection(s).")
    for conn_id in affected:
        print(f"  {conn_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
