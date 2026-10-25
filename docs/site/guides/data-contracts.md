# Data contracts (ODCS)

A data contract is a team's written promise about a dataset: its columns, the quality rules it
keeps and how fresh it stays. DataQ reads contracts in the
[Open Data Contract Standard](https://bitol-io.github.io/open-data-contract-standard/) (ODCS) v3
and turns them into ordinary suites and checks that run, alert and score like any other. This is
the **consumer** side: DataQ checks data that has already landed against the contract. It does not
enforce anything inside the producer's pipeline.

## Import a contract

Send the ODCS document, as YAML text or JSON, with the connection the data lives on:

```bash
curl -X POST "$DATAQ/api/v1/contracts/import" \
  -H "Authorization: Bearer $DATAQ_TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --arg yaml "$(cat orders.odcs.yaml)" --arg conn "$CONNECTION_ID" \
        '{connection_id: $conn, document_yaml: $yaml}')"
```

DataQ validates the document against the ODCS JSON Schema for its `apiVersion` (v3.0.0 to
v3.2.0; a newer v3 minor is accepted with a warning; v2 and v4 are refused), stores it exactly as
sent, and creates **one suite per schema object**, targeted at the object's `physicalName`
(`schema.table`, `catalog.schema.table` on Unity Catalog, a path on object stores). The response
lists the suites it created, any object whose name did not resolve to a table on that connection
(its suite exists but cannot run until you set a target), and every clause it could not enforce.

Importing requires the Member role. Each version of a contract is stored once; importing the same
`id` and `version` again is refused unless you send `replace: true`.

## What becomes a check

| In the contract | In DataQ |
|---|---|
| `required: true` | not-null check |
| `unique: true`, a single `primaryKey` | uniqueness check |
| several `primaryKey` properties | compound uniqueness |
| `logicalTypeOptions` minimum / maximum (exclusive too), length, pattern | range, length and regex checks |
| `enum` / `invalidValues` with `validValues` or `pattern` | value-set or regex check |
| `nullValues`, `missingValues`, `duplicateValues` | not-null, not-in-set, uniqueness checks; a `percent` bound becomes the fail threshold |
| `rowCount` | a volume monitor; a one-sided bound stays one-sided |
| the object's property list | a schema-drift check whose baseline is the declared columns |
| `type: sql` rules | a custom SQL check that fails when the query's result breaks the operator, or is empty |
| `type: custom` with `engine: great-expectations` | that expectation, if DataQ allows it |
| `slaProperties` `latency` on a timestamp column | a freshness monitor |
| `classification` of `confidential`, `restricted`, `pii`, `sensitive` or `secret` | the column joins the suite's PII columns, so failing samples mask it |
| `severity` `info` / `warning` / `error` / `critical` | warn / warn / fail / critical; no severity means fail |
| `dimension` | DataQ's dimension (`conformity` reads as validity; `coverage` stays unclassified) |

A schedule for an object's suite comes from a `dataq.schedule` cron in the object's
`customProperties`, and `dataq.maxAgeHours` (with `dataq.freshnessColumn`) adds a freshness
ceiling.

### Not enforced

Everything else is kept on the contract as **not enforced**, with the reason, and never silently
dropped. That includes `frequency` (a refresh cadence is not a staleness limit; state the limit as
`latency`), `retention` and the other service-level properties, `text` rules, rules for another
engine such as Soda, a `rows` bound on `nullValues`, `multipleOf`, and any check DataQ's own
validators refuse for that datasource.

## A new version

Import the new version with `suite_ids` set to the suites the old version created. DataQ re-points
them and reconciles their checks: clauses that are new get a check, a check nobody has edited since
the import is updated to the new clause, a check whose clause disappeared is **switched off** (not
deleted, so its history stays), and checks people added to the suite themselves are left alone.
Without `suite_ids`, the new version creates fresh suites.

## Export

`GET /api/v1/contracts/{id}/export` returns the contract as an ODCS document (JSON, or YAML with
`?format=yaml`): the stored document plus `dataq.*` custom properties naming the check behind each
rule, and every check people added to the suites as a `type: custom, engine: dataq` rule.
Importing the export recreates every check.

## Who sees a contract

You see a contract when you can view at least one of its suites; the response counts the linked
suites you cannot see without naming them. Workspace admins also see contracts with no linked
suite. Deleting a contract needs edit on all of its suites, and only unlinks them: no suite, check
or result is deleted.

## Contracts and comparison checks

A [comparison check](datasources-checks.md) reconciles two datasets against each other. A contract
describes one dataset against a written promise. They answer different questions and can be used
together.
