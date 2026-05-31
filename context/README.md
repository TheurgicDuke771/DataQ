# context/

Read-only product reference material that informed the v1 plan but is **not part
of the codebase**. Nothing here is executed, imported, or kept in sync with the
implementation automatically.

**Authority:** where this folder and [`docs/adr/`](../docs/adr/README.md) disagree,
the **ADRs win** — they record decisions made *after* this material and
deliberately supersede parts of it (e.g. ADF/Airflow are orchestration providers,
not datasources; DQX is deferred to v1.1). Treat the roadmap here as the original
intent, not the current contract.

## Contents

| File | Purpose |
|---|---|
| `DataQ_platform_roadmap.md` | The original 8-week / 100-task product roadmap. Mirrored — with execution status — in [`docs/progress.md`](../docs/progress.md). |

## Notes

- Internal Azure resource names have been replaced with neutral placeholders
  (e.g. `example-adf-preprod`) so the public repo doesn't expose real naming
  conventions. Substitute your own when provisioning.
