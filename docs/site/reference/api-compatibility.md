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
| **Python client** (`dataq-client`) | The convenience layer (`DataQClient` and its methods, the typed run result) and the `dataq` command line: commands, flags and exit codes. |
| **Personal access tokens** | The `Authorization: Bearer dq_live_...` scheme. |

## Not covered

These can change in any release without notice:

- Anything the API marks internal: paths under `/api/v1/_probe/`, and fields or endpoints the
  OpenAPI spec does not document.
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

## The export document's `version`

The `version` field is the compatibility lever for the suite document:

- A change to the document's shape that an older importer would misread **bumps** `version`.
  Additive fields do not.
- `import` refuses a version it does not know, rather than guessing at an older or newer layout.
- After a bump, the server keeps importing the **previous** version for at least one minor
  release, so documents exported before an upgrade can still be imported after it.

## The Python client's version

`dataq-client` is versioned with DataQ itself: client `1.x.y` is built from server `1.x.y`'s
spec. A client works against a server of the **same minor version**, and against the next
minor version for everything that version did not deprecate. Upgrade the client when you
upgrade the server.
