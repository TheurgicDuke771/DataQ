"""Full-stack API E2E smoke against a running DataQ stack (no mocks).

Drives the **real** HTTP API the way the frontend does — dev-bypass auth, the
seeded demo dataset — and asserts the read + authoring paths work end-to-end
(HTTP → service → Postgres). Run it after `docker compose up` + the seed:

    python -m backend.scripts.e2e_smoke                 # http://localhost:8000
    DATAQ_API=http://localhost:8000 python -m backend.scripts.e2e_smoke

Against a DEPLOYED stack (real auth), point DATAQ_API at the public frontend
(its nginx proxies /api to the internal api) and pass a bearer token:

    DATAQ_API=https://<frontend-host> DATAQ_BEARER=$(az account get-access-token \
        --resource api://<api-app-id> --query accessToken -o tsv) \
        python -m backend.scripts.e2e_smoke

Note the authoring round-trip (create suite → check → delete) writes to — and
cleans up from — whatever workspace the token can edit; the connection-type
assertion expects the demo/harness connection set.

What it verifies (exit 0 = all passed):
  1. the six seeded connection types are listed (secrets never returned);
  2. the demo suites + their checks are retrievable;
  3. an authoring round-trip: create suite → add a check → read back → delete;
  4. a dry-run *attempt* returns a structured result/error, not a crash
     (live connectivity fails-soft without real datasource creds — expected).

Live `test()`/runs against real Snowflake/S3/etc. are out of scope (no creds —
the documented deferred smoke); this proves the app layer, not the datasources.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

import httpx

API = os.environ.get("DATAQ_API", "http://localhost:8000")
BASE = f"{API}/api/v1"

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    mark = "PASS" if ok else "FAIL"
    if ok:
        _passed += 1
    else:
        _failed += 1
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # Dev-bypass: no bearer needed when AUTH_DEV_BYPASS=true + environment=dev.
    # Deployed: DATAQ_BEARER carries the Azure AD access token (same token the
    # web UI sends), enabling the live-smoke run per the deferred-smoke plan.
    bearer = os.environ.get("DATAQ_BEARER")
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    client = httpx.Client(base_url=BASE, timeout=30.0, headers=headers)

    # 1. Connections — six types present, no secret leaked.
    r = client.get("/connections")
    conns: list[dict[str, Any]] = r.json() if r.status_code == 200 else []
    types = {c["type"] for c in conns}
    expected_types = {"snowflake", "s3", "adls_gen2", "unity_catalog", "adf", "airflow"}
    check("GET /connections returns 200", r.status_code == 200, f"status={r.status_code}")
    check(
        "all six connection types seeded",
        expected_types.issubset(types),
        f"missing={expected_types - types}",
    )
    check(
        "secret is never returned on a connection",
        all("secret" not in c and "secret_ref" not in c for c in conns),
        "a connection payload leaked a secret field",
    )

    # 2. Suites + checks retrievable.
    r = client.get("/suites")
    suites: list[dict[str, Any]] = r.json() if r.status_code == 200 else []
    check("GET /suites returns the demo suites", len(suites) >= 3, f"count={len(suites)}")
    orders = next((s for s in suites if s["name"] == "Orders quality"), None)
    check("'Orders quality' suite present", orders is not None)
    if orders is not None:
        r = client.get(f"/suites/{orders['id']}/checks")
        checks = r.json() if r.status_code == 200 else []
        names = {c["name"] for c in checks}
        check(
            "'Orders quality' has its seeded checks",
            {"order_id not null", "amount in range"}.issubset(names),
            f"got={sorted(names)}",
        )
        amount = next((c for c in checks if c["name"] == "amount in range"), None)
        check(
            "severity thresholds round-trip on a check",
            amount is not None and amount["fail_threshold"] == 5,
            f"check={amount}",
        )

    # 3. Authoring round-trip: create suite → add check → read → delete.
    # Bind to a *datasource* connection (Snowflake) — suites target datasources,
    # not orchestration providers (ADF/Airflow); this also routes the dry-run
    # below through the real Snowflake path instead of an unsupported-type 422.
    conn_id = next((c["id"] for c in conns if c["type"] == "snowflake"), None)
    if conn_id is not None:
        name = f"e2e-smoke-{uuid.uuid4().hex[:8]}"
        r = client.post("/suites", json={"name": name, "connection_id": conn_id})
        check("POST /suites creates a suite", r.status_code == 201, f"status={r.status_code}")
        sid = r.json()["id"] if r.status_code == 201 else None
        if sid:
            r = client.post(
                f"/suites/{sid}/checks",
                json={
                    "name": "smoke not null",
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "config": {"column": "id"},
                },
            )
            check("POST a check into the suite", r.status_code == 201, f"status={r.status_code}")
            r = client.get(f"/suites/{sid}/checks")
            check("the new check reads back", r.status_code == 200 and len(r.json()) == 1)

            # 4. Dry-run against the real Snowflake path: with no live creds it
            # fails soft as a structured 502, not a 500 crash. (200 if creds were
            # somehow present; a 422 here would mean the type was wrongly rejected.)
            r = client.post(
                f"/suites/{sid}/checks/dryrun",
                json={
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "config": {"column": "id"},
                    "table": "ORDERS",
                    "schema": "PUBLIC",
                },
            )
            check(
                "dry-run on the Snowflake path fails soft (502 structured, no crash)",
                r.status_code in (200, 502),
                f"status={r.status_code}",
            )

            r = client.delete(f"/suites/{sid}")
            check(
                "DELETE the smoke suite cleans up", r.status_code == 204, f"status={r.status_code}"
            )

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
