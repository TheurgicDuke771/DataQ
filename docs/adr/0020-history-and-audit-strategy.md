# ADR 0020 — History/audit strategy: per-entity Type-4 snapshot tables, no SCD-2

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0009](0009-flat-monorepo-layout.md), [0010](0010-provider-agnostic-infrastructure-seams.md) (least-privilege / secrets in the SecretStore), [0013](0013-marketplace-distribution-and-anti-lock-in.md) (BYOL portability); issues [#310](https://github.com/TheurgicDuke771/DataQ/issues/310) (this decision), [#308](https://github.com/TheurgicDuke771/DataQ/issues/308)/[#309](https://github.com/TheurgicDuke771/DataQ/issues/309) (the consistency-hardening follow-ups surfaced alongside it)

## Context

The internal Postgres data has no general history/versioning framework. **Alembic versions the *schema* (DDL), not the *data*** — it cannot answer "what was this connection's config last Tuesday." A consistency/SCD review (2026-06-20) found:

- **One real history mechanism exists:** `check_versions` — an immutable snapshot of a check's editable state written on create + every successful update (effectively **SCD Type 4**, a separate history table). It backs the check version-history drawer.
- Everything else is **SCD Type 1** (overwrite + `updated_at`): `connections` (config/credential changes), `suites`, `trigger_bindings`, `shares`. No prior-value retention.
- **All deletes are hard deletes**; no soft-delete (`deleted_at`) anywhere; no **SCD Type 2** (`valid_from`/`valid_to` versioned rows) anywhere.

The question: what history/audit posture should v1.x take, and specifically — do we adopt SCD Type 2?

**Why SCD-2 is a poor fit here.** This is a richly-linked OLTP graph (`suites→connections`, `checks→suites`, `runs→suites`, `results→runs/checks`, `shares`, `trigger_bindings`, the `*_versions` tables). True SCD-2 makes the entity id non-unique (one row per version), which breaks every foreign key that points at it. The only way to get Type-2 semantics without destroying the FK model is a *separate* history table per entity — which is **Type 4**, i.e. exactly what `check_versions` already is. Full SCD-2's cost is dominated by **ongoing maintenance**, not the build: every read needs a temporal predicate (`valid_to IS NULL`) or a current-only view, every new column must be mirrored, history grows unbounded (retention/partitioning), and the close-then-insert write adds its own concurrency surface. That tax is unjustified for a single-tenant DQ platform.

## Decision

1. **No SCD Type 2.** Reject versioned-row dimensions; they fight the FK model and carry a permanent maintenance tax.
2. **Per-entity Type-4 snapshot tables where config history has a concrete need.** Extend the `check_versions` pattern entity-by-entity rather than build a framework. **`connection_versions` is added now** (the first such extension beyond checks) — connection name/config history, snapshot on create + name/config update.
3. **Credentials are never snapshotted.** Secrets live only in the SecretStore (referenced by the constant `conn-<id>` pointer); history tables hold only editable, **non-secret** fields. A credential rotation (`reauth`, or a secret-only update) records **no** version — that is an audit-log concern, not config history (ADR 0010).
4. **Cascade-delete is accepted: history is not retained past entity deletion.** Both `check_versions` and `connection_versions` use `ondelete=CASCADE` on the entity FK; deleting the entity drops its history. (`changed_by` is `SET NULL` so a snapshot outlives its *author*, just not its entity.) Retention-past-delete would need a tombstone/soft-delete; explicitly **out of scope** for v1 — revisit only if an audit/compliance requirement appears.
5. **No soft-delete in v1.** Hard deletes stay. (A deleted connection orphaning the *meaning* of past runs is a known, accepted limitation.)
6. **A cross-entity audit log (`actor, entity, action, before/after, ts`) is deferred, not rejected.** It is the right tool if/when "who changed this credential/share, and to what" becomes a requirement — cheaper and more uniform than per-entity versioning for *audit* (vs *config history*). Tracked in #310.

## Consequences

**Positive**
- Smallest viable surface: history is additive Type-4 tables (backward-compatible migrations, no FK redesign, no read-path rewrite), reusing a proven pattern + UI shape.
- Credentials provably stay out of history; the snapshot is safe to expose through the read API.
- Portable for BYOL (ADR 0013): plain tables, no Postgres-temporal or extension dependency.

**Negative / watch**
- History is **lost on delete** by design — if compliance later needs retention-past-delete, revisit (4).
- Per-entity history means **N decisions/migrations**, one per entity that needs it (checks ✅, connections ✅; suites/triggers not yet) — deliberate, to avoid a premature framework.
- No unified audit trail yet; credential-rotation events in particular are unrecorded until the audit log lands (#310).

## Alternatives considered

- **Full SCD-2 (versioned rows or a library like SQLAlchemy-Continuum):** rejected — FK-model break + permanent query/maintenance tax, disproportionate for single-tenant v1.
- **Generic audit log first:** strong option (one hook, covers everything) and still on the table for *audit*; but it gives before/after events, not the first-class "version history" view the product already surfaces for checks (and now connections). The two are complementary — Type-4 for config history now, audit log when audit is required.
- **Soft-delete everywhere:** deferred — adds a `deleted_at IS NULL` predicate to every read for a need v1 doesn't have.
