# Suite document

A suite written as a file: its name, description and checks, with no database identity and
no connection. Export produces one, import creates a suite from one, and validate tells you
whether an import would be accepted. Keep it in a repository beside the pipeline it guards.

The document can be JSON or YAML. Both mean exactly the same thing.

```yaml
version: 1
name: Orders quality
description: Checks on the orders table after the nightly load
checks:
  - name: order id is never null
    expectation_type: expect_column_values_to_not_be_null
    config:
      column: order_id

  - name: status is a known value
    expectation_type: expect_column_values_to_be_in_set
    config:
      column: status
      value_set: [NEW, PAID, SHIPPED, CANCELLED]
      mostly: 0.99
    warn_threshold: 0.5
    fail_threshold: 2

  - name: orders arrived today
    kind: freshness
    expectation_type: monitor:freshness
    config:
      column: order_ts
    warn_threshold: 24
    fail_threshold: 48
```

## Fields

### Document

| Field | Required | Meaning |
|---|---|---|
| `version` | no (default `1`) | The document format. This page describes version `1`; any other value is refused. |
| `name` | yes | The suite's name, 1–128 characters. |
| `description` | no | Up to 1024 characters. |
| `checks` | no | A list of checks. An empty suite is valid. |

An unknown field anywhere in the document is refused, so a typo in a field name is an error
and never a silently ignored line.

### Check

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | 1–256 characters. |
| `expectation_type` | yes | The check type. Every type, its parameters and a worked example of each is on [Check types](check-types.md). |
| `kind` | no (default `expectation`) | `expectation`, `freshness`, `volume`, `schema_drift`, `anomaly`, `aggregate` or `comparison`. Must match the type: a `monitor:freshness` type has kind `freshness`. |
| `config` | no | The type's parameters, as shown for each type on [Check types](check-types.md). |
| `warn_threshold`, `fail_threshold`, `critical_threshold` | no | Severity bands. They must be ordered warn ≤ fail ≤ critical, and only types that measure a number accept them. |
| `dimension` | no | `accuracy`, `completeness`, `consistency`, `integrity`, `timeliness`, `uniqueness` or `validity`. **Leave the field out** to have it derived from the check type. Write `dimension: null` to keep the check unclassified on purpose. |
| `engine` | no (default `gx`) | What evaluates the check. A native engine (`dmf`, `dqx`) imports only onto a connection that offers it. |
| `source_connection` | comparison checks only | `{name, env}` of the connection the comparison reads its source from. It must already exist in the workspace under that name and environment. |

## What the document does not carry

- **The connection.** You name it when you import or validate, so the same file can be
  applied to DEV and to PROD.
- **The target table or file.** An imported suite has no target and cannot run until you set
  one, in the app or through the API.
- **Schedules, triggers, sharing and notification settings.** These belong to the suite in
  the workspace, not to the document.
- **Run history.** Importing always creates a new suite. It never updates an existing one.

## YAML rules

DataQ reads YAML with JSON's rules for plain values, so a value is what it looks like:

| You write | DataQ reads |
|---|---|
| `true`, `false` | a boolean |
| `42`, `-2`, `1.5`, `1e3` | a number |
| `null`, `~`, or nothing | null |
| anything else, quoted or not | text |

So `NO`, `yes`, `off`, `2026-01-01`, `1:30` and `010` are all text. Many YAML tools would
read those as a boolean, a date or a number; DataQ does not, and a country code or a
zero-padded identifier in a `value_set` stays what you wrote. To pass a number or `true` as
text, quote it: `"42"`.

Not supported, and refused with the line number:

- anchors and aliases (`&name`, `*name`) and merge keys (`<<`)
- explicit tags (`!!binary`, `!!timestamp`, …)
- keys that are not text
- documents longer than 1,000,000 characters

## Validate before you import

`POST /api/v1/suites/validate` runs every rule import applies and reports **all** the
problems, where import stops at the first. It creates nothing.

```bash
curl -sS https://dataq.example.com/api/v1/suites/validate \
  -H "Authorization: Bearer $DATAQ_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg c "$CONNECTION_ID" --rawfile y orders.yaml \
        '{connection_id: $c, document_yaml: $y}')"
```

```json
{
  "valid": false,
  "check_count": 3,
  "problems": [
    {
      "location": "checks[1]",
      "check_name": "status is a known value",
      "code": "check_config_invalid",
      "message": "warn_threshold (2) must be <= fail_threshold (0.5) — severity thresholds band an increasingly bad metric, so they must be non-decreasing"
    }
  ]
}
```

That is the example above with the second check's `warn_threshold` and `fail_threshold`
swapped. `location` is `document` for a problem with the document as a whole, `checks[1]` for the
second check, or a field path such as `checks[1].expectation_type` when a field is missing or
misspelled. Branch on `code`, not on the message.

`valid: true` means an import onto that connection would be accepted. It does **not** mean
the checks would pass, or even run: validation opens no datasource, so it cannot tell you
that a column named in a check does not exist. A file that cannot be read as YAML at all is
a `422` with the line and column.

Validation is against a connection because some rules depend on its type: a native-engine
check, a type that only runs on a dataframe, a comparison's source.

## Import and export

| To | Call |
|---|---|
| Export as JSON | `GET /api/v1/suites/{id}/export` |
| Export as YAML | `GET /api/v1/suites/{id}/export?format=yaml` |
| Import JSON | `POST /api/v1/suites/import` with `{connection_id, document}` |
| Import YAML | `POST /api/v1/suites/import` with `{connection_id, document_yaml}` — the YAML as one text value |

Send `document` or `document_yaml`, never both. Import and validate need the Member role or
higher; export needs view access to the suite.

The format is covered by the [API compatibility](api-compatibility.md) policy.
