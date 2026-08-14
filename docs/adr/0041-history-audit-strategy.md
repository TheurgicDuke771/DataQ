# ADR 0041 — History & audit posture for v1.x+: one append-only audit log; no soft-delete; cascade stands

- **Status:** Accepted
- **Date:** 2026-08-13
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR-0020 (decision 6 flips deferred → accepted; decisions 1/4/5 are re-affirmed on new evidence)
- **Related:** ADR [0020](0020-history-and-audit-strategy.md) (the v1 posture this closes out), [0027](0027-suite-permission-model-workspace-admin.md) / [0033](0033-workspace-roles-rbac.md) (the grants an audit log has to record), [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) (accrete-not-delete for assets/lineage; machine-written rows), [0039](0039-openbao-self-hosted-secret-backend.md) (secrets live behind the `SecretStore`, never in a DB row); issues [#310](https://github.com/TheurgicDuke771/DataQ/issues/310) (this decision), [#431](https://github.com/TheurgicDuke771/DataQ/issues/431) (G1 data-*access* audit trail — phase 2 of decision 3), [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) (G2 erasure), [#540](https://github.com/TheurgicDuke771/DataQ/issues/540) / [#541](https://github.com/TheurgicDuke771/DataQ/issues/541) / [#753](https://github.com/TheurgicDuke771/DataQ/issues/753) (the delete-path FK work), [docs/compliance-posture.md](../compliance-posture.md)

## 1. Context — what changed since ADR 0020

ADR 0020 (2026-06-20) settled the v1 posture: per-entity **Type-4 snapshot tables** where config history has a concrete need (`check_versions`, then `connection_versions`), **no SCD-2**, **no soft-delete**, **cascade-delete accepted**, and a cross-entity audit log **deferred, not rejected** — "the right tool if/when *who changed this credential/share, and to what* becomes a requirement."

That requirement has arrived, from two directions at once:

- **Compliance.** The posture audit ([docs/compliance-posture.md](../compliance-posture.md)) names the missing audit trail **G1 (#431)** — the single hard blocker for any PHI deployment (HIPAA §164.312(b)) — and says in as many words: *revisit ADR 0020*. #431 is scheduled (v1.1 W7 stretch), so the substrate decision has to land ahead of it or #431 invents its own.
- **Authz surface growth.** ADR 0027 made the workspace-admin an implicit admin on **every** suite — so a suite share is now the finest-grained grant there is, and **granting or revoking one leaves no trace of any kind today** (`share_service.py:176`, `POST`/`DELETE /suites/{id}/shares`). ADR 0033 will add a stored, mutable `users.role` on top (#740–#744, **unbuilt** — there is no `users.role` column and no role-change route yet; workspace-admin is still the `WORKSPACE_ADMIN_EMAILS` env allowlist, changed by redeploy). ADR 0033 §7 already names a durable record of role changes as a requirement, so the substrate has to exist before that slice lands, not after.

### Verified state of the four claims in #310 (2026-08-13, against `main`)

| # | Claim | Verdict |
|---|---|---|
| 1 | `check_versions.check_id` is `ondelete=CASCADE`, so history dies with the check (and with its suite) | **Confirmed** — `models.py:751`; `connection_versions.connection_id` is the same (`:547`). `changed_by` is `SET NULL` on both (`:776`, `:560`), so a snapshot outlives its author but never its entity. |
| 2 | No general audit log | **Confirmed** — no table carries `(actor, action, before, after, ts)`. The nearest neighbours are ownership-only `created_by` columns (there is **no `updated_by` anywhere**), `incidents.acknowledged_by`/`resolved_by_user_id`, and `api_keys.last_used_at`, whose own comment reads "telemetry, not an audit ledger" (`api_key_service.py:41`). The request log line carries `method`/`path`/`status` and **no principal** (`main.py:221-260`). |
| 3 | All deletes are hard deletes; "a deleted connection orphans the meaning of past runs" | **Half stale.** Hard deletes confirmed — 8 `session.delete` sites, **zero** `deleted_at`/`is_deleted`/`archived` columns in `backend/app` or in any of the 47 migrations. But the connection half of the motivation was closed by #753/#541: `delete_connection` refuses with a **409** while any suite runs against the connection (`connection_service.py:915-923`) or any comparison check names it as source (`:924-936`), with a TOCTOU `IntegrityError` backstop. **A connection can no longer be deleted out from under its runs.** What remains is *suite* deletion, which cascades checks → runs → results (#540) and is irrecoverable. |
| 4 | No SCD Type 2 anywhere | **Confirmed.** |

So the honest remaining scope of #310 is narrower than when it was filed: **(b)'s headline motivation has already been met by a cheaper mechanism**, and what is actually missing is the record of *deliberate acts by a principal* — which is (c).

#310 put four options on the table: **(a)** preserve `check_versions` history past its check's deletion, **(b)** soft-delete for connections/suites so run lineage survives, **(c)** one cross-entity audit log (`actor, entity_type, entity_id, action, before, after, ts`), **(d)** full SCD Type 2. Verdicts follow, one per numbered decision.

## 2. Decision

### 1. (c) **Accepted.** One append-only `audit_events` table, and it is the substrate #431 fills in — not a second mechanism.

```
audit_events(
  id, occurred_at,
  action_class,            -- 'config' (phase 1) | 'access' (phase 2, #431)
  action,                  -- 'check.update', 'share.grant', 'connection.reauth', …
  entity_type, entity_id,  -- NO foreign key. See below.
  actor_user_id → users.id ON DELETE SET NULL,
  actor_kind,              -- user | pat | webhook  (see below — NOT 'system')
  actor_label,             -- denormalized identity at time of action
  before JSONB, after JSONB,
  request_id
)
```

**`entity_id` deliberately carries no foreign key.** An FK leaves two options, both self-defeating: cascade (the audit row dies with the entity — exactly the failure this table exists to fix) or restrict (the audit log makes deletion impossible). The in-repo precedent is `check_versions.source_connection_id`, a plain UUID with a deliberate no-FK comment (`models.py:765-768`). This is the structural property that lets one table answer what a Type-4 snapshot table **cannot**: the delete event itself.

**`actor_kind` has no `system` value, deliberately.** A webhook is an *external* principal (the orchestrator) and a PAT is a user's, but decision 1 below rules out machine-written rows entirely — so a `system` value would have no legitimate producer and would exist purely as an invitation to smuggle routine machine writes in under it. Add it only when a config-changing act by DataQ *on its own authority* actually exists, and name that act in the same change.

**Phase 1 (this decision, #310) is config/action events. Phase 2 (#431/G1) is data-read events, on the same table**, discriminated by `action_class` so retention, indexing and — if read volume ever demands it — physical partitioning can diverge per class without a second schema, a second authz gate, or a second redaction seam. #431 does **not** get its own table; it gets rows.

**The two phases have opposite latency contracts, and that is deliberate.** A phase-1 event is written **inside the mutation's own transaction**: if the audit write fails, the mutation rolls back, so an applied change and its record can never diverge (fail-closed, and the change that "isn't recorded" also didn't happen). A phase-2 read event must **not** sit in a read's critical path (#431's own AC-3 forbids the regression); how it gets off the path is #431's decision, not this one.

**Audit = deliberate acts by a principal.** Machine-written rows are explicitly out: a run insert, a `lineage_edges` refresh, an `assets.last_seen` bump, an inventory sync, a retention purge, **and the bulk-DML deletes** (the #770 asset sweep, the sample-failure purge, the OTP-code and lineage-edge prunes). Auditing those would bury the actor-attributable events in noise. This is also why the "8 `session.delete` sites" of §1 is the **ORM** delete surface only — the bulk-DML deletes bypass both `session.delete` and the route-table guard of decision 8, correctly, because no principal issued them. The one existing exception stays where it is: the retention purge already stamps `results.sample_failures_purged_at`.

### 2. (a) **Rejected as stated; the need is met by decision 1.**

No `RESTRICT`, no `SET NULL`, no tombstone on `check_versions.check_id`. Each of the three shapes #310 proposes is worse than the audit log at the job:

- `RESTRICT` makes a check with history undeletable, and *every* check gets a version on create — i.e. nothing would ever be deletable.
- `SET NULL` leaves orphan rows with no entity to identify them; #310 itself concedes they would need "self-contained identity" — a denormalized name/suite copy. That is a second audit log, scoped to one entity, with worse ergonomics.
- A tombstone is soft-delete for checks only, carrying all of (b)'s costs across none of its breadth.

The audit log records **create/update/delete for every entity, with before/after**, so the full edit history of a deleted check is reconstructible from it — including the final state, which is the one thing a cascading Type-4 table structurally cannot retain. **Cascade-delete (ADR 0020 §4) stands, now for a positive reason rather than an accepted cost.**

The two mechanisms keep distinct jobs, and the split is the point: **Type-4 tables are the product feature** (the version-history drawer, restore #1120 — they must be joinable, queryable-by-`version_no`, and safe to expose through the read API); **the audit log is the durable record** (append-only, admin-gated, outlives its entity). Only one of them needs to survive deletion.

### 3. (b) **Re-deferred — and the deferral is narrower and better-argued than ADR 0020's.**

No `deleted_at` on `connections`, `suites`, or anything else. Three reasons, in ascending order of force:

1. ADR 0020's original reason still holds and has *grown*: a `deleted_at IS NULL` predicate on every read, across a query surface that has since gained assets, lineage, incidents, rollups and scorecards.
2. **The column is the easy part; "deleted means inert" is the work.** A soft-deleted connection still owns a live SecretStore credential and still has `*_secret_name` refs (`connection_service.py:938-946`); a soft-deleted suite still matches trigger bindings, schedules, and the beat dispatcher. Every one of those needs an explicit rule, and each is a place to get it silently wrong.
3. **A delete that does not delete is a compliance liability, not a compliance feature.** GDPR Art 17 erasure (G2/#432) wants the opposite of what soft-delete provides. Adding soft-delete to satisfy an audit requirement would create work for the erasure requirement filed one gap below it.

**What the real need gets instead:** the connection half is already solved (409 guard, §1 claim 3). The suite half — an irreversible, silent destruction of every run and result — gets the audit log's delete event (**what** was destroyed, **by whom**, **when**) plus a delete confirmation that states the blast radius before the fact (follow-up filed). That is honesty about an irreversible action, which is what was actually missing; it is not undo, and this ADR does not pretend otherwise.

### 4. (d) **Rejected permanently, not deferred.**

ADR 0020's rationale is unchanged — SCD-2 makes the entity id non-unique, which breaks every FK in a richly-linked OLTP graph, and its cost is dominated by permanent maintenance (a temporal predicate on every read, every new column mirrored, unbounded growth, a close-then-insert concurrency surface). Two things since make it worse, not better: the entity surface a Type-2 mirror would have to track column-for-column has grown (`check.dimension` per ADR 0038, `check.engine` per 0036, the asset/incident tables per 0034), and the question SCD-2 was proposed to answer is now answered by decision 1 at a fraction of the cost. Recorded as **closed**, so it is not re-litigated.

### 5. Which entity gets which treatment

| Entity | Config history (Type-4) | Audit events (phase 1) | Delete posture |
|---|---|---|---|
| `checks` | ✅ `check_versions` (exists) | create · update · delete · restore | direct delete **+** CASCADE from suite — **stands** |
| `connections` | ✅ `connection_versions` (exists) | create · update · **reauth/credential rotation** · delete | direct delete, behind a 409 guard while suites/comparison-source checks exist |
| `suites` | ❌ none, and none added — the audit log covers it | create · update · **target change** · delete | direct delete, cascades checks/runs/results |
| `shares` (ADR 0027 grants) | ❌ | ✅ **grant · revoke — the highest-value rows in the table, and un-recorded today** | direct delete **+** CASCADE from suite and from user |
| `users` | ❌ | ✅ provision/signup (ADR 0032) · profile update · delete | **no delete path exists** — three `created_by` FKs lack `ondelete` (#1319); sign-in/sign-out session lifecycle is deliberately **out of phase 1** |
| `users.role` (ADR 0033, **unbuilt** — #740–#744) | ❌ | ⏳ prospective: role change incl. last-admin guard trips — the substrate must precede the slice (ADR 0033 §7) | — |
| `trigger_bindings`, `schedules`, `suite_notifications` | ❌ | ✅ create · update · delete | direct delete **+** CASCADE from suite |
| `api_keys` (ADR 0026) | ❌ | ✅ mint · revoke — **never** the token or its hash | revoke-in-place **+** CASCADE from user |
| `assets` (ADR 0034) | ❌ | ✅ **metadata mutation only** (owner, description) | **bulk-DELETE swept** once `last_seen` goes stale (`asset_service.py:297`) — a machine write, not audited. ADR 0034's "not deletes" phrasing describes the *posture*, not the mechanism; the rows do go. |
| `incidents` | ❌ (has its own actor columns) | ✅ acknowledge · resolve | CASCADE |
| `runs`, `results` (incl. the `sample_failures` / `observed_value` columns), `lineage_edges` | ❌ | ⏳ **phase 2 = read events (#431)** — machine *writes* are never audited | CASCADE |

**`suites` deliberately gets no Type-4 table.** ADR 0020 set the bar at "a concrete need"; the suite's version-history need was never product-driven (there is no suite version drawer), and once the audit log exists, adding one would be a second record of the same events. Recorded as a decision, not an omission.

### 6. Redaction of `before`/`after` payloads

An audit payload is a **JSONB write that never passes through structlog**, so CLAUDE.md §10's standing rule — *redact at the logger, not the call site* — does not cover it. Saying so explicitly matters: assuming coverage here is exactly the #849 shape, in the one place the project's own rule genuinely does not reach.

1. **Allow-list per entity type, never `dict(row)`.** A deny-list fails open the moment a column is added — the #124/#952 shape, where a change silently classified every check in prod. Phase 1 reuses the field sets the Type-4 snapshot builders already declare (`record_check_version`, `record_connection_version`), so there is **one** serializer per entity and it is already reviewed for secret-safety.
2. **No secret values, ever — including "redacted in place".** They are not in the DB to begin with: `connections.config` holds `*_secret_name` **pointers** and `secret_ref`, never a credential (`connection_service.py:127-146`; the `iceberg.py:143-159` validator rejects a password embedded in a URI at the door). A `connection.reauth` event therefore records **that** the credential rotated and **which pointer** — never a before/after of the value. This positively records the event ADR 0020 §3 left unrecorded, without weakening §3.
3. **No warehouse data, in either phase.** `results.sample_failures` and `results.observed_value` are the incidental PII/PHI stores; copying them into an append-only table with a *longer* retention would silently defeat the #1253 purge and the G2 erasure path. Phase-2 read events record **which** result was read, never **what** it contained.
4. **Reuse the credential subset of `_PII_KEYS` as a final pass — not the whole set.** `_PII_KEYS` contains `name`, `display_name` and `user_id` (`logging.py:66-68`), which are tuned for log lines and are actively wrong here: `name` is the *content* of a rename event and `actor_label` is the *point* of the actor record. So the belt-and-braces pass uses the credential keys only (`password`, `secret`, `token`, `api_key`, `private_key`, `passphrase`, `catalog_secret`, …) plus `_scrub_secret_strings`, with a drift guard pinning that subset to `_PII_KEYS` so it cannot silently shrink (the same arrangement `logging.py:100-104` already uses for the `dq_live_`/`dq_sess_` prefixes).
5. **The actor is itself personal data.** `actor_user_id` is `SET NULL` (the event outlives the user) and `actor_label` is denormalized so attribution survives that null. The G2 tension is real and named here rather than discovered later: an Art-17 erasure must be able to **pseudonymize `actor_label` in place** while keeping the event and its timestamp. The machinery is #432's; the column shape that makes it possible is this ADR's.
6. **Payload cap with loud truncation.** A custom-SQL body or a `schema_drift` baseline can be large; the payload is capped and a truncation marker is stored. No silent caps — the same rule as ADR 0040 §5.

### 7. Append-only is a guard against accidental mutation — **not** tamper-resistance, and the difference is recorded

No UPDATE or DELETE code path in the app, **plus** `REVOKE UPDATE, DELETE ON audit_events FROM dataq_app`.

**What that buys, stated honestly, because an earlier draft of this section overclaimed it.** The deployed app owns its schema: the Azure stack creates the database `OWNER dataq_app` and hands the migrate job the *same* `DATABASE_URL` as the api and worker, so Alembic will create `audit_events` owned by `dataq_app` — and a table's owner can `GRANT` the privileges back in one statement, or `TRUNCATE`/`DROP` it regardless of any grant. The REVOKE therefore stops an accidental in-app `UPDATE`/`DELETE` (a stray ORM call, a careless bulk statement) and nothing stronger. It is worth doing and it is not tamper-evidence.

**Splitting the role to make it stronger is explicitly rejected.** A second, less-trusted role in the `dataq` database is precisely what the project's standing Postgres constraint forbids: the referential-integrity check runs implicit casts as the *referenced table's owner*, an unpatched escalation whose whole mitigation is that `dataq_app` is the only role in there. Buying weak tamper-resistance at the price of that mitigation is a bad trade, and the trade is named here so #1318 does not discover it. **Retention therefore runs as `dataq_app`** — a dedicated maintenance task on its own clock (`AUDIT_RETENTION_DAYS`, default 365), deliberately **not** coupled to `sample_failures_retention_days` (they protect opposite things: one keeps a record, the other destroys one) — with the ownership caveat above understood rather than papered over.

Real tamper-evidence — #431's word — needs cryptographic chaining anchored *outside* the database, and is **deferred to #431** where the requirement actually lives.

### 8. Coverage cannot rest on remembering the call site

48 POST/PATCH/PUT/DELETE routes exist under `backend/app/api/v1/`, plus MCP tools, webhook receivers and beat tasks. That 48 is the **denominator the guard scans, not the work**: many are not config mutations at all (`POST /suites/{id}/run`, `POST /connections/test`, `POST /runs/{id}/cancel`, the `auth/otp/*` endpoints) and belong on the exemption list. An explicit service-layer call at each real mutation is the right mechanism — it is the only one that can distinguish a principal's intent from a machine write, per decision 1 — but a new endpoint that forgets it is invisible.

The guard is a test that enumerates FastAPI's **route table** (`app.routes`, filtered to POST/PATCH/PUT/DELETE under `/api/v1`) and asserts each has audit coverage or sits on an explicit, justified exemption list. Enumerating the route table rather than an audit registry is the load-bearing part: ADR 0039's orphan-secret sweep shipped an introspection guard that iterated *the models already registered with it*, making a new model invisible to the very check meant to catch it. A route appears in `app.routes` whether or not anyone remembered the audit.

## 3. Consequences

**Positive**

- One additive, backward-compatible table answers every question #310 raised, plus the credential-rotation gap ADR 0020 shipped as a known hole, plus the share-grant history ADR 0027/0033 created a need for — with no FK redesign, no read-path predicate, and no change to any existing table.
- #431/G1 becomes an *increment* (rows + a non-blocking write path) instead of a parallel mechanism. The compliance-posture doc's *"Revisit ADR 0020"* instruction is **answered here**; [the doc itself is edited in #1318](https://github.com/TheurgicDuke771/DataQ/issues/1318) (its G1 entry still points at 0020 and still says "v2.x target", while #431 now sits in the v1.1 W7 milestone). A decision is not discharged by another document asserting that it is.
- The Type-4 tables keep doing the one job they are good at, and stop being asked to be an audit trail they structurally cannot be.
- Portable: a plain table, no Postgres temporal features, no extension — BYOL-safe per ADR 0013/0031.

**Negative / accepted**

- **History is still lost on entity deletion in the *product* surface.** The version drawer for a deleted check is gone; only the audit log (admin-gated) can reconstruct it. Accepted — the audience for "what did this deleted check look like" is an auditor, not the check's editor.
- **Suite deletion remains irreversible and destroys runs/results.** The audit log records it; it does not undo it. This is the honest residue of rejecting (b).
- **Every mutation path grows an explicit call.** Mitigated by decision 8, not eliminated — a beat task or MCP tool added outside the HTTP route table is still on the author.
- **A write amplification on the mutation path**, and a same-transaction failure mode: a broken audit write fails the mutation. Deliberate (fail-closed), and the reason phase 2 is not allowed the same contract.
- **The audit log retains a record about a deleted entity and a deleted user.** That is its purpose and it is in tension with erasure; decision 6.5 names the pseudonymize-in-place requirement so #432 inherits a solvable problem rather than a contradiction.
- **Three `created_by` FKs still have no `ondelete`** (`connections.created_by`, `suites.created_by`, `schedules.created_by` — `models.py:393/617/1013`; these are the only three `users.id` FKs lacking one), latent only because v1 has no user-delete API. G2 erasure will make them live; filed as #1319 rather than left as residue of #541.
- **Append-only is not tamper-proof**, and decision 7 says so out loud rather than implying otherwise: `dataq_app` owns the table and can re-grant what the `REVOKE` removed, and splitting the role is rejected on a stronger security constraint. An operator who needs true tamper-evidence needs #431's external anchor.

## 4. Alternatives considered

- **Auto-audit via SQLAlchemy event listeners / `SQLAlchemy-Continuum`** — rejected. It audits *flushes*, which cannot tell a principal's deliberate act from a `last_seen` bump, an inventory sync, or a result insert; the signal would drown on day one. It also re-imports the SCD-2 maintenance tax decision 4 rejects.
- **`updated_by`/`updated_at` columns on every entity** — rejected. Type-1 by construction (one actor, overwritten), no action verb, no before/after, and nothing survives the delete. It is strictly less than the audit log at comparable cost.
- **A separate `access_events` table for #431** — rejected, and it was the closest call. Read volume and mutation volume differ by orders of magnitude and want different retention. But `action_class` gives them different retention and different indexes inside one table, while a split would duplicate the authz gate, the redaction seam, the admin query surface and the tamper-evidence story — and would make "everything that happened to this suite" a UNION. If read volume ever demands separation, partitioning by `(action_class, occurred_at)` is a physical change that touches no API.
- **Structured logs as the audit trail** — rejected, and worth recording because it looks free. Logs are redacted by design (that is the whole point of `_PII_KEYS`), carry no principal on the request line, and live in a retention-managed telemetry sink outside our control. The compliance-posture doc already states this: PII redaction is precisely why logs cannot serve as the audit trail.
- **Soft-delete + an audit log** — rejected as redundant for the audit need and harmful for the erasure need; decision 3.
- **Postgres temporal tables / `periods` extension** — rejected. Extension dependency, contra BYOL portability (ADR 0013/0031), and it implements the SCD-2 semantics decision 4 rejects on their merits.

## 5. Follow-ups filed

- [#1318](https://github.com/TheurgicDuke771/DataQ/issues/1318) — **phase 1 build**: `audit_events` + the service seam + config-mutation coverage + the route-table guard + the `REVOKE` grant.
- [#1319](https://github.com/TheurgicDuke771/DataQ/issues/1319) — the three `created_by` FKs with no `ondelete` (residue of #541; live once G2 erasure lands).
- [#1320](https://github.com/TheurgicDuke771/DataQ/issues/1320) — suite-delete confirmation must state its blast radius (the mitigation that makes rejecting (b) honest).
- [#431](https://github.com/TheurgicDuke771/DataQ/issues/431) — **phase 2**, unchanged in scope, now landing on this table rather than inventing one.
