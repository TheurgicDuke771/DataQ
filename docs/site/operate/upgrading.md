# Upgrading

How to move a running DataQ to a newer release without losing data.

## How releases work

- Releases are tagged `vX.Y.Z` and follow [Semantic Versioning](https://semver.org/). The
  backend and frontend images carry the same tag.
- The [changelog](../reference/changelog.md) lists what changed in each release. An entry
  marked **⚠️ breaking** needs action from you; read every such entry between the version you
  run and the one you are moving to.
- Database migrations only add to the schema in the release that needs them. A column or
  table is removed in a later release than the code that stopped using it, so the previous
  release keeps working while a new one rolls out.

## Before you upgrade

1. **Read the changelog** from your version to the target version. Note any one-time step an
   entry asks for.
2. **Back up the database.** For the prebuilt-image stack, stop the stack and copy the data
   directory (`./dataq-data`, or the path in `DATAQ_DATA_DIR`). For a managed Postgres, take a
   snapshot or a `pg_dump`.
3. **Pin the target version.** Do not upgrade onto the moving `latest` tag.

## The prebuilt-image stack

The compose file and the images change together, so take the compose file from the release
you are moving to and pin both images to it:

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/vX.Y.Z/docker-compose.ghcr.yml
export DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

The `migrate` service brings the database to the new schema before the API and the worker
start; they do not start if it fails. Your connections, suites, results and stored
credentials are in the data directory and are kept.

## A cloud deployment

Run the **Deploy** workflow on the release tag (see [Deployment](deployment.md)). It builds
the images, runs the migration job and waits for it to succeed, and only then rolls the API,
the worker and the frontend. If the migration fails, nothing is rolled.

Infrastructure changes are separate: if the changelog says the OpenTofu stack changed, run
`tofu plan` and `tofu apply` on it first.

## After you upgrade

- `/healthz` returns `200` and you can sign in.
- A suite run completes. This exercises the worker, the secret store and a datasource
  together.
- Run any one-time step the changelog asked for.
- Hard-refresh the browser if the app looks unchanged. The entry page is revalidated on every
  load, but an open tab keeps the bundle it already loaded.

## Rolling back

Roll back by **restoring the backup you took** and starting the previous release on it:

1. Stop the stack (or scale the API and the worker to zero).
2. Restore the data directory, or the database snapshot, from before the upgrade.
3. Re-pin the previous image tag, with the compose file from that release, and start.

Re-pinning the previous tag **without** restoring does not work once the upgrade has applied a
migration: the older release's migration step does not know the newer schema revision and
stops, so the API and the worker never start. Anything written after the upgrade is lost by a
restore, so decide quickly.

If the changelog shows the release you upgraded to added no database migration, re-pinning
the previous tag alone is enough.

## Skipping versions

You can upgrade straight to the latest release: the migration step applies every migration
between your schema and the new one, in order. Read the changelog for every release you
skip.
