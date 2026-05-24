---
name: Bug report
about: Report a defect. Per working-agreement #3 — defects MUST be raised here before being fixed; the PR that fixes it must reference this issue with `Fixes #N`.
title: "fix: <short description>"
labels: ["bug"]
assignees: []
---

## What happened

<!-- Plain description of the defect. -->

## What you expected to happen

<!-- The correct behaviour. -->

## Reproduction steps

1.
2.
3.

## Environment

- Component: <!-- e.g. backend / frontend / celery worker / mcp / docker-compose / azure deploy -->
- Branch / commit: <!-- git rev-parse HEAD -->
- OS / browser (if relevant):
- Datasource (if relevant): <!-- snowflake / adls / s3 / unity_catalog / n/a -->
- Orchestration provider (if relevant): <!-- adf / airflow / n/a -->

## Severity

- [ ] critical — production down / data loss / security
- [ ] high — major feature broken, no workaround
- [ ] medium — broken feature with workaround
- [ ] low — cosmetic / minor annoyance

## Logs / screenshots / error messages

<details>
<summary>Logs</summary>

```
paste here
```

</details>

## Additional context

<!-- Anything else useful: related PR, suspected root cause, recent change that introduced it. -->
