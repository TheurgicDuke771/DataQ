# API compatibility

What DataQ promises to keep working when you upgrade, and how it tells you when something has
to change. It covers every surface other software builds against: the REST API, the MCP tools,
the suite export document and the Python client.

## Stable surfaces

These are covered by this policy. A change to them follows the rules below.

| Surface | What is stable |
|---|---|
| **REST API** (`/api/v1/...`) | Paths, HTTP methods, request fields and their meaning, response fields and their meaning, status codes, and the error envelope (`error.code`, `error.message`). The published [OpenAPI spec](rest-api.md) is the contract. |
| **MCP tools** (`/mcp`) | Tool names, parameter names and meaning, and the documented fields of each tool's result. |
| **Suite export document** | The JSON shape `export` produces and `import` accepts, versioned by its top-level `version` field. |
| **Python client** (`dataq-client`) | The convenience layer (`DataQClient` and its methods, the typed run outcome) and the `dataq` command line: commands, flags and exit codes. |
| **Personal access tokens** | The `Authorization: Bearer dq_live_...` scheme. |

## Not covered

These can change in any release without notice:

- The smoke-test probe under `/api/v1/_probe/`. It appears in the OpenAPI spec but is a
  development hook, not part of the contract.
- Fields or endpoints the OpenAPI spec does not document.
- The web UI, its routes and its internal calls.
- The database schema, task queues, log lines and metric names.
- The Python modules inside the server image.
- Error **messages**. Branch on `error.code` (REST) or the error type, never on the wording.
- The Python client's generated layer beyond what the REST contract already promises: it is
  regenerated from the spec, so it changes exactly when the spec does.

## What counts as breaking

A change is **additive** (no notice needed) when an existing client keeps working unchanged:

- a new endpoint, MCP tool, CLI command or flag;
- a new **optional** request field or tool parameter;
- a new response field, or a new value in an enum.

Build clients to tolerate these: ignore response fields you do not know, and treat an unknown
enum value as "other" rather than failing.

A change is **breaking** when an existing, correct client could stop working:

- removing or renaming a path, field, tool, parameter, command, flag or exit code;
- making an optional request field required, or narrowing what a field accepts;
- changing a field's type or meaning, or the meaning of a status code;
- changing the export document's shape without changing its `version`.

Request validation is strict: an unknown request field is **rejected** (422), never silently
ignored, so a misspelled field fails loudly rather than being dropped.

## How a breaking change is made

1. **It is announced** in the [changelog](changelog.md) under a **Breaking** heading in the
   release that deprecates it, with the replacement and the planned removal release.
2. **It is deprecated before it is removed.** The old form keeps working for **at least one
   minor release** after the one that announces it. REST endpoints scheduled for removal say
   so in the OpenAPI spec (`deprecated: true`).
3. **It is removed only in a minor or major release**, never a patch release, and the
   changelog says so again.

The one exception is a **security fix**. When keeping the old behaviour would leave a
vulnerability open, the change can take effect immediately. It is still announced under
**Breaking**, with the reason.

## Recorded exceptions

Changes that took effect without the deprecation window above, and why:

| Release | Change | Why no deprecation window |
|---|---|---|
| Unreleased | `POST /connections`, a config- or credential-changing `PATCH /connections/{id}`, and `POST /connections/{id}/reauth` return `422 connection_test_failed_on_save` and write nothing when the connection's test fails; `PUT /admin/llm` does the same for an enabled provider (`422 llm_test_failed_on_save`). | A request this now refuses used to save a connection that could not work, and a failing re-auth used to overwrite a working credential. A client that wants the old behaviour sends the new optional `skip_test: true`, so no client is left without a path. |

## The export document's `version`

The `version` field is the compatibility lever for the suite document:

- **Any** change to the document's shape bumps `version`, an added field included: `import`
  rejects fields it does not know, so an older server would refuse a document carrying one.
- `import` refuses a version it does not know, rather than guessing at an older or newer layout.
- After a bump, the server keeps importing the **previous** version for at least one minor
  release, so documents exported before an upgrade can still be imported after it.

## The Python client's version

`dataq-client` is versioned with DataQ itself: client `1.x.y` is built from server `1.x.y`'s
spec. **Use the client version that matches your server.** The generated layer validates
responses strictly, so an older client can refuse a value a newer server added (a new enum
value, for instance). The convenience layer and the `dataq` command line read a run's status as
plain text and are not affected. Upgrade the client when you upgrade the server.
