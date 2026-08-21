"""Disclosure seam for **live-probe** routes (#1419, #1479).

A live probe opens a datasource with stored credentials, returns real cell
values, and **persists nothing**: the column profiler, the check dry-run, and
the column lister. Both of the product's data-protection pipelines — the
redaction ladder and the G1 access audit (ADR
[0041](../../../docs/adr/0041-history-audit-strategy.md)) — hang off a persisted
`Result` row. A live probe has none, so it fell outside **both**, for one
structural reason. That is why #1419 (dry-run returns an unredacted
`observed_value`) and #1479 (profiler and dry-run emit no access event) are one
defect wearing two hats, and why the fix is one seam rather than four patches.

Four doors is not an incidental number. The recurring failure in this codebase
is *a guard applied at one door and not its sibling* — it recurred five times in
the ADR 0033 track alone. Dry-run and profiler are siblings; REST and MCP are
siblings again. Routing every probe through one function is what makes the fifth
probe inherit both properties instead of re-deriving them.

## The rule: redaction follows the DESTINATION, not the column

DataQ's column policy asks *"is this column sensitive?"* — a property of the
data. Every regulation the product cites asks something else: whether a
**principal** may disclose to a **destination** for a **purpose**. HIPAA's
minimum-necessary standard *permits* access for legitimate work and requires it
be **logged**; §164.312(b) is an audit control, not a redaction mandate. GDPR
Art 6 is a lawful basis for processing, not a forbidden-field list. Clinicians
are not shown redacted charts — their access is recorded.

A single boolean therefore cannot be right in both directions, and DataQ had
both errors at once: results were masked for the person who needed them, while
live probes disclosed silently. **The missing state was never "mask more" — it
was "disclose accountably."**

So:

* `Destination.INTERACTIVE` — a human's own authoring screen. Ephemeral,
  deliberately requested, `edit`-gated on a suite they can already modify, and
  the values are the entire point (you cannot tell whether a rule fires without
  seeing what it fired on). **Full values, always audited.**
* `Destination.EGRESS` — the value outlives the moment or leaves the product: an
  LLM context that may quote it onward, a downloaded file, an alert payload, a
  persisted result. **Redacted, always audited.**

This makes an existing inconsistency correct rather than accidental. MCP's
`dryrun_check` already redacted while the REST route did not; under a
column-property rule that is a contradiction, under a destination rule it is the
right answer twice — an assistant's context is egress, an author's screen is not.

## What this deliberately does NOT do

It does not weaken `column_policy.require_classification` (G3 fail-closed). An
operator who turns that on has explicitly chosen assurance over capability, and
`INTERACTIVE` collapses to masked for them. The default preserves the feature;
the operator keeps the ability to trade it away. That asymmetry is the point —
DataQ should not make that trade on their behalf, and should not prevent it
either.

It also does not claim that an `edit` holder seeing PII is free of risk. The
containment is that `edit` is not universal (ADR 0027 per-suite grants, ADR 0033
Viewer clamp), plus the audit trail this module makes unconditional. What
changes is that the disclosure stops being **invisible**, which is the property
that actually blocked reasoning about it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from backend.app.services import audit_service

# The redaction ladder lives in `run_service` and is imported wholesale rather
# than partially re-implemented here. Every name below is one DataQ already
# decided once: the masking sentinel (shared with `core.logging`), the
# per-column sensitivity verdict, and the fail-closed switch. A second spelling
# of any of them would drift silently — the governance-tag floor (#415 level 1,
# G3) being the layer a reimplementation forgets first.
from backend.app.services.run_service import (
    _REDACTED_VALUE,
    _SENSITIVE_TAG_VALUES,
    _known_sensitive,
    _may_show_incidental,
    _policy_requires_classification,
)

__all__ = [
    "Destination",
    "applicable_tags",
    "mask_profile_columns",
    "record_probe_access",
    "redact_probe_observed_value",
    "sensitive_profile_columns",
    "values_are_masked",
]


class Destination(StrEnum):
    """Where the probed values are going. See the module docstring.

    A `StrEnum` so it survives a JSONB round-trip into the audit payload
    unchanged — an investigator reading `detail.destination` should see
    ``"interactive"``, not ``"Destination.INTERACTIVE"``.
    """

    #: A human's own authoring surface. Ephemeral, edit-gated, not persisted.
    INTERACTIVE = "interactive"
    #: The value outlives the moment or leaves the product — LLM context,
    #: downloaded file, alert payload, stored result.
    EGRESS = "egress"


def values_are_masked(policy: Mapping[str, Any] | None, *, destination: Destination) -> bool:
    """Whether this probe must mask cell values before returning them.

    The whole policy decision, in one place, so the four call sites cannot drift
    from each other — which is the failure mode this module exists to prevent.

    `EGRESS` always masks. `INTERACTIVE` masks **only** under fail-closed mode
    (`column_policy.require_classification`), because there the operator has
    explicitly chosen assurance over capability and it is not DataQ's place to
    override that in either direction.

    Note this returns a decision about *masking*, never about *access*: a probe
    that masks still happened, still touched the customer's warehouse with a
    stored credential, and is still audited. Conflating the two is what produced
    the silent-disclosure state in the first place.
    """
    if destination is Destination.EGRESS:
        return True
    return _policy_requires_classification(policy)


def applicable_tags(
    tags: Mapping[str, str] | None, *, probed_other_target: bool
) -> Mapping[str, str] | None:
    """The asset's tags, filtered for a probe that may not be reading that asset.

    A probe can name an explicit table/path that is **not** the suite's asset, and
    two tables can share a column name. The asset's tag map is then about the
    wrong table, and it carries two kinds of entry that fail in opposite
    directions:

    * a **sensitive** tag masks — applying it to the wrong table can only
      over-mask, which is safe;
    * a **non-sensitive** tag is a *clearance* that un-masks in fail-closed mode —
      applying that to the wrong table hands out permission that was never
      granted for this data.

    So the clearances are dropped and the floor is kept. An earlier version of
    this code dropped the whole map and the comment claimed that "can only mask
    more, never less" — which is backwards: dropping the map also drops the
    sensitive floor, and outside fail-closed mode that makes it mask *less*. On
    the common path (the check editor sends the suite's own table explicitly) that
    would have silently disabled the G3 governance floor.
    """
    if not tags:
        return tags
    if not probed_other_target:
        return tags
    return {
        column: value
        for column, value in tags.items()
        if str(value).strip().lower() in _SENSITIVE_TAG_VALUES
    }


def redact_probe_observed_value(
    observed: dict[str, Any] | None,
    *,
    tested_column: str | None,
    policy: dict[str, Any] | None,
    tags: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Thin call into `run_service.redact_observed_value`.

    Used to add the **scalar** `observed_value` case that redactor did not
    cover (#1482 — an expectation whose observed value is a single cell, e.g.
    ``expect_column_max_to_be_between`` on a text column, passed through raw
    even under fail-closed mode with the column named in `pii_columns`). That
    gap is now closed in the shared redactor itself, so every scalar case —
    numeric or not — masks there under the same authority as the list case,
    and this wrapper has nothing left to add.
    """
    from backend.app.services.run_service import redact_observed_value

    return redact_observed_value(observed, tested_column=tested_column, policy=policy, tags=tags)


def _profile_sample_values(column: Any) -> list[Any]:
    """The cell values a profile actually carries, for the name/value classifier.

    A profile is not a row sample, so the classifier gets what there is: the
    `top_values` contents plus the min/max endpoints. Passing an empty list
    instead would silently disable the *value* half of the classifier and leave
    only the column-name heuristic — which is the half that misses a column
    called `field_7` full of SSNs, the exact case fail-closed mode exists for.
    """
    values: list[Any] = [t.get("value") for t in (getattr(column, "top_values", None) or [])]
    for endpoint in (getattr(column, "min_value", None), getattr(column, "max_value", None)):
        if endpoint is not None:
            values.append(endpoint)
    return values


def sensitive_profile_columns(
    columns: Sequence[Any],
    *,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None = None,
    destination: Destination = Destination.INTERACTIVE,
) -> list[str]:
    """Names of the profiled columns the redaction ladder considers sensitive.

    Delegates to `run_service._known_sensitive` rather than re-deriving the
    ladder: a second implementation of "is this column sensitive" is the same
    guard-at-one-door-and-not-its-sibling shape this module exists to remove, and
    the two would diverge invisibly — the governance-tag floor (#415 level 1, G3)
    is exactly the layer a reimplementation would forget.

    Reported even when nothing is masked. On an `INTERACTIVE` disclosure the
    values are shown *and* the audit event names which of them were sensitive,
    which is the whole content of "accountable rather than forbidden".
    """

    # The rung differs by destination, which is the same principle one level
    # deeper rather than a new one.
    #
    # `INTERACTIVE`: a profiled column was *explicitly named by the caller*, so it
    # is the analogue of a **tested** column — shown unless KNOWN sensitive. Using
    # the incidental rule here would default-mask every column a classifier cannot
    # positively clear, which on a free-text table is most of them, and profiling
    # would stop answering the question it exists for.
    #
    # `EGRESS`: match the persisted-results path, which default-masks incidental
    # columns (#415). Otherwise an unclassifiable `notes` column ships in full to
    # a model that may quote it onward, while the SAME column is masked in a
    # stored run's sample — and an assistant seeing a value in one place and a
    # mask in the other cannot tell which is the truth.
    if destination is Destination.EGRESS:
        return [
            column.column
            for column in columns
            if not _may_show_incidental(column.column, _profile_sample_values(column), policy, tags)
        ]
    return [
        column.column
        for column in columns
        if _known_sensitive(column.column, _profile_sample_values(column), policy, tags)
    ]


def mask_profile_columns(columns: Sequence[Any], *, sensitive: Sequence[str]) -> list[Any]:
    """Blank cell *values* on sensitive columns, keeping every statistic.

    `min_value`, `max_value` and each `top_values[].value` are real cell
    contents and mask. `null_count`, `null_fraction`, `distinct_count` and the
    per-value `count` are **statistics about** the data, not the data, and stay —
    which is what keeps a masked profile useful: "how complete is this column,
    how many distinct values, how skewed" is answerable without seeing one cell.

    Returns new objects; the inputs are left alone so a caller cannot leak a
    half-masked structure by holding the original.
    """
    if not sensitive:
        return list(columns)
    blocked = set(sensitive)
    out: list[Any] = []
    for column in columns:
        if column.column not in blocked:
            out.append(column)
            continue
        out.append(
            replace(
                column,
                min_value=None,
                max_value=None,
                top_values=[
                    {"value": _REDACTED_VALUE, "count": t.get("count")}
                    for t in (column.top_values or [])
                ],
            )
        )
    return out


def record_probe_access(
    session: Session,
    *,
    action: str,
    suite_id: uuid.UUID,
    actor: Any | None,
    destination: Destination,
    masked: bool,
    values_in_scope: bool = True,
    columns: Sequence[str] | None = None,
    sensitive_columns: Sequence[str] | None = None,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "user",
) -> None:
    """Record that a live probe disclosed (or masked) live warehouse data.

    Keyed on the **suite**, not a run or result, because a live probe creates
    neither — that absence is precisely what made these routes invisible to a
    pipeline keyed on result ids.

    `exposed` means *real cell contents left the boundary*, which is the same
    question `_exposed_result_ids` answers for run results: it is `True` when the
    values were returned unmasked, regardless of whether any column was
    classified sensitive. That is deliberate. An investigator asking "who saw
    cell values from this table" must get every such event; narrowing to
    policy-flagged columns would hide disclosures from columns nobody had
    classified yet, and *unclassified* is the normal state (ADR 0038 makes
    derivation deliberately partial, so NULL means unclassified, never safe).
    `detail.sensitive_columns` is what narrows the set afterwards.

    Never raises — it delegates to `audit_service.record_access`, which writes in
    a SAVEPOINT and commits on its own. A failed audit write must not turn a
    working preview into a 500 (ADR 0041 AC-3); the honest cost is that a gap
    here is only visible as `audit_access_write_failed` at ERROR.
    """
    payload: dict[str, Any] = {"destination": destination.value, "masked": masked}
    if not values_in_scope:
        # A probe that returns only column NAMES never had cell values to mask.
        # Reporting `masked: true` there would assert a redaction that never
        # happened; `values_in_scope: false` says the honest thing, and `exposed`
        # is False because nothing was disclosed rather than because it was hidden.
        payload["masked"] = False
        payload["values_in_scope"] = False
    if columns:
        payload["columns"] = list(columns)
    if sensitive_columns:
        payload["sensitive_columns"] = list(sensitive_columns)
    if detail:
        payload.update(detail)

    audit_service.record_access(
        session,
        action=action,
        entity_type="suite",
        entity_id=suite_id,
        actor=actor,
        # Masked probes are recorded with `exposed=False` rather than dropped:
        # "this person profiled the customers table and saw nothing" is a
        # different fact from "nobody profiled it", and only one of them is true.
        exposed=masked is False and values_in_scope,
        detail=payload,
        actor_kind=actor_kind,
    )
