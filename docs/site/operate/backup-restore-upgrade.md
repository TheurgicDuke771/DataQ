# Backup, restore & rollback

DataQ's own PostgreSQL database holds every connection, suite, check, run, result, incident
and audit event you have. This page is how to keep a copy of it, how to put that copy back,
how to move between releases on it, and how to undo a release that went wrong.

## What to back up

| Deployment | Database | Stored credentials | Also |
|---|---|---|---|
| Prebuilt-image stack (`docker-compose.ghcr.yml`) | `pg_dump` of the `dataq` database, **or** the `postgres/` directory under `DATAQ_DATA_DIR` with the stack stopped | the `openbao/` directory under `DATAQ_DATA_DIR` — the vault's raft data **and** its seal key live there together | the compose file and `.env` you started it with. The generated CA is rebuilt from the data directory on the next start; a copy of the `ghcr_local_ca` volume saves re-trusting it |
| Cloud deployment with Azure Key Vault, AWS Secrets Manager or a managed OpenBao / Vault | a Postgres snapshot or `pg_dump` | **nothing to dump** — the vault is the system of record, with its own backup. What you must keep is the **list of secret names** DataQ wrote (every connection stores its `*_secret_name`, so a database restore and the vault must agree) | the OpenTofu state and the environment variables of the api, worker and beat |
| `SECRET_STORE=env` (evaluation only) | `pg_dump` | the environment itself — it is the secret store | — |

A database backup without the matching secret store restores every connection as
**un-credentialed**: the rows come back, the credential they point at does not. Re-enter
them with **Re-authenticate** on each connection. The reverse — a vault without the database
— is a set of secrets nothing refers to.

### The database: `pg_dump -Fc`

Use the custom format: it restores selectively, in parallel, and carries the schema version
with the data.

```bash
# As the database owner (the role DataQ connects as, or a superuser). The custom
# format is compressed; one file per backup.
pg_dump --format=custom --no-owner --no-privileges \
  --dbname "postgresql://dataq@<host>:5432/dataq" \
  --file "dataq-$(date -u +%Y%m%dT%H%MZ).dump"
```

`--no-owner --no-privileges` keeps the dump restorable into a database owned by a different
role, which is what a new environment or a managed Postgres gives you. The application role
needs no more than it already has: `pg_dump` reads.

On the prebuilt-image stack, run it inside the Postgres container so the client matches
the server:

```bash
docker compose -f docker-compose.ghcr.yml exec -T postgres \
  pg_dump -U dataq --format=custom --no-owner --no-privileges dataq > dataq.dump
```

Take the backup **before** every upgrade, and on a schedule that matches how much history
you are prepared to re-create: a nightly dump loses at most a day of runs. A managed
Postgres (Azure Database for PostgreSQL, Amazon RDS) has point-in-time recovery; turn it on
and keep the logical dump as well, because point-in-time recovery does not leave the
account, and a dump does.

### What a dump contains, and does not

A dump is one consistent snapshot: every table as of the instant `pg_dump` started, including
`alembic_version` (which schema revision the data is at) and the audit hash chain's head.

It does **not** contain:

- **Runs in flight.** A run that was executing at the snapshot is restored as `running`
  with no worker behind it. The stuck-run reaper marks it failed after its timeout; trigger
  it again if you need the result.
- **Queued work.** The Celery queue lives in Redis, which is not backed up: a scheduled or
  triggered run that was queued but not started is gone. Schedules themselves are rows and
  come back; the next due slot fires normally.
- **Rate-limit and admission counters** (Redis) — they reset, which is harmless.
- **Stored credentials** — see the table above.

## Restore

1. **Stop writers.** Stop the api, worker and beat (`docker compose stop api worker beat`, or
   scale the three services to zero). A restore under a running worker produces a database
   that disagrees with what the worker believes it is doing.
2. **Restore into an empty database.** Keep the broken one until the restored one is verified.
   ```bash
   createdb --owner dataq dataq_restored
   pg_restore --no-owner --no-privileges --dbname dataq_restored dataq-<stamp>.dump
   ```
   On the prebuilt-image stack the equivalent is `docker compose exec -T postgres
   pg_restore -U dataq --no-owner --no-privileges -d dataq_restored < dataq.dump`.
3. **Point DataQ at it** (`DATABASE_URL`, or rename the databases) and make sure the secret
   store is the one the dump was taken beside.
4. **Verify before you start the worker.**
    - The schema revision matches the images you are about to run:
      `cd backend && alembic current` prints one revision and no error. `alembic heads` on
      the same tree is the revision these images expect; if `current` is behind it, run
      `alembic upgrade head` (the migrate job does this for you); if `alembic current` fails
      with *Can't locate revision*, the dump is from a **newer** release than the images —
      see [Rolling back](#rolling-back).
    - `GET /healthz` returns 200 with the api up.
    - Sign in; connections, suites and run history are what you expect.
    - As a workspace admin, `GET /api/v1/admin/audit-events/verify` returns `status: ok`.
      The chain restores intact — the head pointer and the events are in the same snapshot —
      and events written after the restore chain on from the restored head.
5. **Start the worker and beat**, then **run one suite** end to end. That exercises the secret
   store, the datasource and the results path together; a restore whose credentials are
   missing fails here, not at 03:00 when the first schedule fires.

### What a restore loses

Everything written after the snapshot: runs, results, incidents opened or resolved, checks
edited, members added, audit events. Decide quickly whether to restore — the older the
backup relative to the failure, the more you re-create by hand.

The **audit chain** deserves one note. The chain itself verifies clean after a restore. If
you publish the chain head to an external anchor (`TAMPER_ANCHOR=webhook`), the anchor holds
heads that the restored database never reaches: that is the restore, visible from outside,
which is exactly what the anchor is for. Record the restore (when, from which backup) where
you keep the anchor, so a later audit can tell a restore from tampering.

## Upgrading

The sequence, for any deployment:

1. **Back up** (above). The backup is also the rollback of last resort.
2. **Read the release notes** for every release between yours and the target. Each release
   states its **Rollback** line (below), any one-time backfill script, and any migration
   that needs a step **before** it runs.
3. **Pull the release's images** — pinned to the tag, never `latest`.
4. **Run the migration job.** On the prebuilt-image stack the `migrate` service runs
   `alembic upgrade head` before the api and the worker start; on a cloud deployment the
   Deploy workflow runs the migrate job and rolls nothing until it has succeeded.
5. **Roll the images** — api, worker, beat, frontend — and check each one is on the new tag.
6. **Run the release's one-time steps** (backfill scripts named in the notes).
7. **Verify** as after a restore: `/healthz`, sign-in, one suite run.

### The guarantee behind it

Every migration is **additive**: it adds a nullable column, a column with a default, a
table or an index, and never drops or renames something the previous release reads. The
code that depends on a new column ships in a release *after* the migration that adds it.

So at any moment during step 4 and step 5, the **previous release's images run correctly
against the new schema** — the migration runs first, under old code, by design. The same
property is what makes rollback cheap.

The upgrade path is tested, not assumed: on every change, CI builds a database at the
previous release's schema, fills it with that release's own demo data, upgrades it to the
current head, downgrades it back to the previous release and upgrades it again. A migration
that cannot do that round trip does not merge. You can run the same check against your own
Postgres before an upgrade:

```bash
UPGRADE_PATH_DATABASE_URL="postgresql+psycopg2://dataq:<password>@<host>:5432/dataq_upgrade_check" \
  python -m backend.scripts.upgrade_path --tag <your current release>
```

It creates and drops the named scratch database and never touches `dataq`.

## Rolling back

Two different situations, and the release notes say which one you are in.

### Image rollback — the normal case

If the release's **Rollback** line reads *previous images on this schema — yes · DB restore
needed — no*, the data written since the upgrade is kept:

1. **Downgrade the schema to the previous release's head** using the **new** release's
   tree (it is the one that knows both revisions):
   ```bash
   cd backend && alembic downgrade <previous head>     # the revision the Rollback line names
   ```
   On the prebuilt-image stack: `docker compose -f docker-compose.ghcr.yml run --rm migrate
   sh -c "cd backend && alembic downgrade <previous head>"`.
2. **Re-pin the previous tag** (compose file and images from that release) and start.
3. Verify: `/healthz`, sign-in, one suite run.

Step 1 is not optional. The previous release's migrate step reads `alembic_version`, finds
a revision its tree has never heard of, fails with *Can't locate revision*, and the api and
worker — which wait for it — never start. Re-pinning the old images on an upgraded schema
therefore looks like an outage, not a rollback. The downgrade drops only what the newer
migrations added (a column or table the old release never read), so everything the old
release knows about survives, including the runs that happened on the new release.

### Restore rollback — when a release says so

A release whose Rollback line reads *DB restore needed — yes* changed data in a way its
downgrade cannot undo (a backfill that rewrote rows, a type change). The notes say why.
Roll back by [restoring the backup from step 1](#restore) and starting the previous
release on it; everything since the upgrade is lost, which is the cost the notes warned
about.

If you did not take the backup, do not downgrade: keep the new release running and fix
forward.

### How a release says so

Each release's notes carry one line:

```
Rollback: previous images on this schema — yes · DB restore needed — no · downgrade target 49bd96efd9fc
```

*Yes / no* is the default and the tested path. A release that breaks either half carries the
line with the other answer **and** an entry under **Breaking** explaining what the downgrade
loses, so the decision is in front of you before the upgrade, not after.

## Point-in-time: which backup to restore

- **A bad release** — the backup from immediately before the upgrade, unless image rollback
  (above) applies, which loses nothing.
- **Deleted or corrupted data** (a suite deleted by mistake, an import that overwrote checks)
  — the most recent backup from before the change. Restoring into a *separate* database and
  copying the rows you need across, rather than replacing the live one, keeps everything
  else that happened since.
- **A lost host** — the latest backup, onto a new host, with the secret store restored or
  reconnected beside it, then the verification list above.

Related: [Upgrading](upgrading.md) for the per-deployment commands,
[Deployment](deployment.md) for the environment variables named here.
