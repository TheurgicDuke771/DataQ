# context/

Product reference + planning material that frames the v1 plan and what comes
after it, but is **not part of the codebase**. Nothing here is executed or
imported. Two flavours live here: **backward-looking** original intent
(`DataQ_platform_roadmap.md`) and the **forward-looking** planning input
(`post-v1-roadmap.md`, `roadmap-v1.2-v1.3.md`) — both feed planning/task-generation, neither is kept in
sync with the code automatically.

**Authority:** where this folder and [`docs/adr/`](../docs/site/adr/README.md) disagree,
the **ADRs win** — they record decisions made *after* this material and
deliberately supersede parts of it (e.g. ADF/Airflow are orchestration providers,
not datasources; DQX became a per-check engine on Unity Catalog connections, ADR 0036). Treat the roadmap here as the original
intent, not the current contract.

## Contents

| File | Purpose |
|---|---|
| `DataQ_platform_roadmap.md` | The original 8-week / 100-task product roadmap. Mirrored — with execution status — in [`docs/progress-v1.md`](../docs/progress-v1.md) (the archived v1 ledger; the live post-v1 tracker is [`docs/progress.md`](../docs/progress.md)). |
| `post-v1-roadmap.md` | The single home for **everything deferred past v1** — design themes (with pointers to the detailed design docs under [`docs/`](../docs/)) **and** the full `v1.1 Backlog` GitHub-milestone issue list (milestone renamed 2026-07-04), mapped by theme. The intended **input for a post-v1 week-wise task generator**. Status lives on the GitHub milestone, not here. |
| `roadmap-v1.2-v1.3.md` | The forward roadmap: the v1.2 thesis (released `v1.2.0`, 2026-10-04) and the v1.3 cycle theme (2026-10-05 → 2026-11-29). The live week plan is in [`docs/progress.md`](../docs/progress.md). |

## Notes

- Internal Azure resource names have been replaced with neutral placeholders
  (e.g. `example-adf-preprod`) so the public repo doesn't expose real naming
  conventions. Substitute your own when provisioning.
