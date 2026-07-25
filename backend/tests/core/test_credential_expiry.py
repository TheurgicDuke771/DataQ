"""Reading an Azure SAS's stated expiry (#838).

The failure this guards against is not "we computed the wrong date" — it is
"we confidently reported a date for something that was never a SAS", or the
mirror image, "we stayed silent on a credential that told us when it dies".
Prod lineage was dark for six days because nobody was told; a wrong date would
have been worse still, because it comes with an alibi.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.app.core.credential_expiry import azure_sas_expiry

# A realistic account SAS. `sig` is a made-up base64-ish blob, not a credential.
_SAS = (
    "sv=2022-11-02&ss=bfqt&srt=sco&sp=rwdlacupiytfx"
    "&se=2026-07-29T05:59:59Z&st=2026-07-28T22:00:00Z&spr=https"
    "&sig=notarealsignature%3D"
)


def test_reads_the_expiry_a_sas_states_about_itself() -> None:
    assert azure_sas_expiry(_SAS) == datetime(2026, 7, 29, 5, 59, 59, tzinfo=UTC)


def test_tolerates_the_leading_question_mark_azure_hands_out() -> None:
    # The portal's "SAS token" field includes the `?`; the SDK's does not. Both
    # get pasted into the credential box, so both must read the same.
    assert azure_sas_expiry(f"?{_SAS}") == azure_sas_expiry(_SAS)


def test_user_delegation_sas_expires_at_the_earlier_of_se_and_ske() -> None:
    # A user-delegation SAS dies when EITHER the token or its signing key does.
    # Reading only `se` would over-promise on precisely the SAS kind Azure
    # recommends — the badge would go quiet days before the credential stops.
    delegation = (
        "sv=2022-11-02&sr=c&sp=rl"
        "&se=2026-08-30T00:00:00Z"
        "&ske=2026-08-01T00:00:00Z&skoid=abc&sktid=def"
        "&sig=notarealsignature%3D"
    )
    assert azure_sas_expiry(delegation) == datetime(2026, 8, 1, tzinfo=UTC)


def test_naive_sas_time_is_read_as_utc_not_local() -> None:
    # SAS times are UTC. Reading one as local time would shift the deadline by
    # the host's offset — a silently wrong date on a machine in another zone.
    sas = "sv=2022-11-02&se=2026-07-29T05:59:59&sig=notarealsignature%3D"
    assert azure_sas_expiry(sas) == datetime(2026, 7, 29, 5, 59, 59, tzinfo=UTC)


def test_date_only_expiry_is_accepted() -> None:
    sas = "sv=2022-11-02&se=2026-07-29&sig=notarealsignature%3D"
    assert azure_sas_expiry(sas) == datetime(2026, 7, 29, tzinfo=UTC)


def test_a_credential_that_is_not_a_sas_is_silent() -> None:
    # An S3 secret key, a Databricks PAT, a Snowflake private key: none of them
    # state a lifetime, and inventing one for them is the whole failure mode.
    #
    # The stand-ins are assembled from parts rather than written out: a literal
    # of the real shape is a *credential-shaped string in a tracked file*, which
    # push protection blocks and CLAUDE.md §11 forbids even for a fake one. The
    # parser only ever sees the assembled value, so the coverage is identical.
    for secret in (
        "",
        "dapi" + "0" * 32,
        "AKIA" + "X" * 16 + "/" + "y" * 24,
        "-----BEGIN PRIVATE " + "KEY-----\nMIIEvQ==\n-----END PRIVATE KEY-----",
    ):
        assert azure_sas_expiry(secret) is None


def test_a_query_string_without_a_signature_is_not_treated_as_a_sas() -> None:
    # `se=` alone is not evidence of a SAS. Requiring `sig` is what makes this a
    # parse rather than a guess — otherwise any `key=value&…` secret that happens
    # to carry an `se` would get a confident, meaningless expiry.
    assert azure_sas_expiry("se=2026-07-29T05:59:59Z&other=1") is None


def test_a_sas_with_an_unreadable_expiry_is_silent_rather_than_raising() -> None:
    # A malformed value must not raise: an exception here would put the offending
    # credential text into a traceback, which is the #536 leak all over again.
    assert azure_sas_expiry("sv=2022-11-02&se=not-a-date&sig=notarealsignature%3D") is None


def test_an_unreadable_ske_does_not_discard_the_readable_se() -> None:
    # Partial garbage must degrade to the expiry we CAN read, not to silence —
    # dropping a good `se` because a sibling field was malformed would turn a
    # readable deadline into "unknown" and remove the warning entirely.
    sas = "sv=2022-11-02&se=2026-07-29T00:00:00Z&ske=garbage&sig=notarealsignature%3D"
    assert azure_sas_expiry(sas) == datetime(2026, 7, 29, tzinfo=UTC)
