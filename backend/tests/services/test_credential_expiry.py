"""A credential's expiry becomes a fact about the connection (#838).

The bug this pins is the one #828 left behind. #828 made an expired credential
visible *once it broke something* — prod lineage had already been dark for six
days by then. But the expiry was knowable the whole time: an Azure SAS prints
`se=` inside itself. Nobody read it.

Two failure modes matter here, and they are not symmetric:

* **Silence on a credential that told us.** The #828 outage, repeated.
* **A confident date on a credential that never said one.** Worse — the product
  would be reassuring people about a token it cannot actually read.

So the tests below check both directions on every path: the date appears where a
credential states one, and NOTHING appears where it does not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.core.secrets import SecretNotFoundError
from backend.app.datasources import registry
from backend.app.db.models import Connection, User
from backend.app.services import connection_service as svc

# An account SAS expiring 2026-07-29. `sig` is a made-up blob, not a credential.
_SAS = "sv=2022-11-02&ss=b&sp=rl&se=2026-07-29T05:59:59Z&sig=notarealsignature%3D"
_SAS_EXPIRY = datetime(2026, 7, 29, 5, 59, 59, tzinfo=UTC)
_LATER_SAS = "sv=2022-11-02&ss=b&sp=rl&se=2027-01-31T00:00:00Z&sig=notarealsignature%3D"
_LATER_EXPIRY = datetime(2027, 1, 31, tzinfo=UTC)

_ADLS_CONFIG = {"account_url": "https://acct.blob.core.windows.net", "container": "data"}
_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}


class FakeStore:
    """In-memory SecretStore."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data: dict[str, str] = data or {}

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


class _UnreachableStore(FakeStore):
    def get(self, name: str) -> str:
        raise RuntimeError("Key Vault unreachable")


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    return user


def _adls(db_session: Any, store: FakeStore, secret: str = _SAS) -> Connection:
    return svc.create_connection(
        db_session,
        name=f"adls-{uuid.uuid4().hex[:8]}",
        conn_type="adls_gen2",
        env="dev",
        config=dict(_ADLS_CONFIG),
        secret=secret,
        created_by=_user(db_session).id,
        secret_store=store,
    )


# ───────────────────── the adapter seam (no DB) ─────────────────────


class TestAdapterSeam:
    def test_adls_reads_the_expiry_out_of_its_sas(self) -> None:
        assert registry.credential_expiry("adls_gen2", _ADLS_CONFIG, _SAS) == _SAS_EXPIRY

    def test_a_type_whose_credential_states_no_expiry_is_silent(self) -> None:
        # Snowflake (password / key-pair), S3 (access key), Unity Catalog (PAT):
        # none of these carry a lifetime. Reporting one would be invention.
        assert registry.credential_expiry("snowflake", _SF_CONFIG, "p@ssw0rd") is None
        assert (
            registry.credential_expiry(
                "s3",
                {"bucket": "b", "region": "eu-west-1", "access_key_id": "AKIAX"},
                "wJalrXUtnFEMI/K7MDENG",
            )
            is None
        )

    def test_dbt_reads_an_adls_artifact_store_and_stays_silent_on_s3(self) -> None:
        # dbt's secret is whatever its artifacts store needs — the very connection
        # whose SAS expiry caused #828. Only the ADLS shape states an expiry.
        adls_cfg = {"project_name": "p", "artifacts_uri": "adls://a/raw", "jobs": ["j"]}
        s3_cfg = {
            "project_name": "p",
            "artifacts_uri": "s3://bucket/raw",
            "jobs": ["j"],
            "region": "eu-west-1",
            "access_key_id": "AKIAX",
        }
        assert registry.credential_expiry("dbt", adls_cfg, _SAS) == _SAS_EXPIRY
        assert registry.credential_expiry("dbt", s3_cfg, _SAS) is None

    def test_iceberg_reads_only_a_sas_shaped_secret_property(self) -> None:
        # The secret fills whichever property the operator named, so the name is
        # the only evidence of its shape. An account key with a SAS-looking value
        # is not a SAS — and a SAS property is.
        base = {"catalog_type": "rest", "catalog_uri": "https://cat.example.com"}
        sas_cfg = {**base, "secret_property": "adls.sas-token.acct"}
        key_cfg = {**base, "secret_property": "adls.account-key.acct"}
        assert registry.credential_expiry("iceberg", sas_cfg, _SAS) == _SAS_EXPIRY
        assert registry.credential_expiry("iceberg", key_cfg, _SAS) is None

    def test_an_adapter_that_blows_up_yields_unknown_not_an_exception(
        self, monkeypatch: Any
    ) -> None:
        # An expiry is an advisory signal; it must never be able to break the
        # connection CRUD path or the sweep it is read from.
        class _Exploding:
            def validate_config(self, raw: dict[str, Any]) -> Any:
                return None

            def test(self, raw: dict[str, Any], secret: str, **_: Any) -> None:
                return None

            def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime:
                raise ValueError("credential is malformed: <the credential>")

        monkeypatch.setitem(registry._ADAPTERS, "adls_gen2", _Exploding())
        assert registry.credential_expiry("adls_gen2", _ADLS_CONFIG, _SAS) is None


# ───────────────────── the write paths (real DB) ─────────────────────


class TestExpiryIsReadWhenTheCredentialIsWritten:
    def test_creating_a_connection_records_its_credential_expiry(self, db_session: Any) -> None:
        conn = _adls(db_session, FakeStore())
        assert conn.credential_expires_at == _SAS_EXPIRY

    def test_a_credential_with_no_stated_expiry_leaves_it_unknown(self, db_session: Any) -> None:
        conn = svc.create_connection(
            db_session,
            name=f"sf-{uuid.uuid4().hex[:8]}",
            conn_type="snowflake",
            env="dev",
            config=dict(_SF_CONFIG),
            secret="p@ss",
            created_by=_user(db_session).id,
            secret_store=FakeStore(),
        )
        assert conn.credential_expires_at is None

    def test_rotating_the_credential_moves_the_expiry_on_the_same_request(
        self, db_session: Any
    ) -> None:
        store = FakeStore()
        conn = _adls(db_session, store)
        svc.update_connection(db_session, conn.id, secret=_LATER_SAS, secret_store=store)
        db_session.refresh(conn)
        assert conn.credential_expires_at == _LATER_EXPIRY

    def test_rotating_onto_a_non_expiring_credential_clears_the_old_date(
        self, db_session: Any
    ) -> None:
        # The stale-warning failure: swap a SAS for a credential with no lifetime
        # and the product would keep counting down to a date that no longer
        # describes anything, until someone re-auths a connection that is fine.
        store = FakeStore()
        conn = _adls(db_session, store)
        assert conn.credential_expires_at is not None

        svc.update_connection(db_session, conn.id, secret="not-a-sas", secret_store=store)
        db_session.refresh(conn)
        assert conn.credential_expires_at is None

    def test_reauth_clears_the_warning_that_prompted_it(
        self, db_session: Any, monkeypatch: Any
    ) -> None:
        # Re-auth is the "fix the expiring token" button. If the badge survived the
        # fix, the next person would rotate an already-rotated credential.
        store = FakeStore()
        conn = _adls(db_session, store)
        monkeypatch.setattr(svc, "test_connection", lambda *a, **k: None)

        svc.reauth_connection(db_session, conn.id, secret=_LATER_SAS, secret_store=store)

        db_session.refresh(conn)
        assert conn.credential_expires_at == _LATER_EXPIRY


# ───────────────────────── the daily sweep ──────────────────────────


class TestSweep:
    def test_it_populates_a_credential_stored_before_the_feature_existed(
        self, db_session: Any
    ) -> None:
        # Every connection in prod today predates this column. Without the sweep
        # they stay unknown until someone happens to rotate them — i.e. the
        # feature would do nothing for the credentials that already exist.
        conn = _adls(db_session, FakeStore())
        conn.credential_expires_at = None
        db_session.commit()

        changed = svc.refresh_credential_expiry(
            db_session, secret_store=FakeStore({f"conn-{conn.id}": _SAS})
        )

        db_session.refresh(conn)
        assert conn.credential_expires_at == _SAS_EXPIRY
        assert changed >= 1

    def test_it_notices_a_credential_rotated_outside_dataq(self, db_session: Any) -> None:
        # How the #828 SAS was actually replaced: in the Azure portal. DataQ never
        # saw the write, so only a re-read can move the date.
        store = FakeStore()
        conn = _adls(db_session, store)
        store.data[f"conn-{conn.id}"] = _LATER_SAS

        svc.refresh_credential_expiry(db_session, secret_store=store)

        db_session.refresh(conn)
        assert conn.credential_expires_at == _LATER_EXPIRY

    def test_an_unreadable_secret_keeps_the_last_known_expiry(self, db_session: Any) -> None:
        # "We couldn't check today" is not evidence the credential stopped
        # expiring. Blanking the date on a Key Vault outage would silence the
        # warning at the exact moment nobody can verify anything.
        conn = _adls(db_session, FakeStore())
        assert conn.credential_expires_at == _SAS_EXPIRY

        svc.refresh_credential_expiry(db_session, secret_store=_UnreachableStore())

        db_session.refresh(conn)
        assert conn.credential_expires_at == _SAS_EXPIRY

    def test_each_connection_is_committed_before_the_next_is_read(
        self, db_session: Any, monkeypatch: Any
    ) -> None:
        # A sweep-long transaction would mean one failure at commit time throws
        # away every OTHER connection's freshly-read expiry — and would let a
        # credential rotated mid-sweep (those paths commit immediately) be
        # clobbered by the sweep's stale in-memory copy: the lost-update shape
        # #841 already fixed once on this table. Asserting the durable state
        # DURING the sweep is what distinguishes per-row commits from a batch;
        # asserting only the end state passes either way.
        first = _adls(db_session, FakeStore())
        second = _adls(db_session, FakeStore())
        for conn in (first, second):
            conn.credential_expires_at = None
        db_session.commit()

        # Count commits at the moment each credential is fetched. (The durable
        # state can't be probed from another connection here — the `db_session`
        # fixture holds the test inside a rolled-back transaction — but the
        # commit ORDERING is exactly what separates per-row from batch.)
        store = FakeStore({f"conn-{first.id}": _SAS, f"conn-{second.id}": _SAS})
        commits = 0
        commits_before_fetch: list[int] = []
        real_commit, real_get = db_session.commit, store.get

        def _counting_commit() -> None:
            nonlocal commits
            real_commit()
            commits += 1

        def _observe(name: str) -> str:
            commits_before_fetch.append(commits)
            return real_get(name)

        monkeypatch.setattr(db_session, "commit", _counting_commit)
        monkeypatch.setattr(store, "get", _observe)
        svc.refresh_credential_expiry(db_session, secret_store=store)

        assert commits_before_fetch == [0, 1], (
            "the first connection must be committed before the second is read; "
            f"commits seen at each fetch: {commits_before_fetch}"
        )

    def test_one_unreadable_secret_does_not_abort_the_rest_of_the_sweep(
        self, db_session: Any
    ) -> None:
        # A single bad connection must not leave every later one unknown.
        class _OneBadStore(FakeStore):
            def get(self, name: str) -> str:
                if name == bad_ref:
                    raise RuntimeError("Key Vault unreachable")
                return super().get(name)

        good = _adls(db_session, FakeStore())
        bad = _adls(db_session, FakeStore())
        bad_ref = f"conn-{bad.id}"
        good.credential_expires_at = None
        db_session.commit()

        svc.refresh_credential_expiry(
            db_session, secret_store=_OneBadStore({f"conn-{good.id}": _SAS, bad_ref: _SAS})
        )

        db_session.refresh(good)
        assert good.credential_expires_at == _SAS_EXPIRY
