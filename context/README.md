# context/

Product reference + planning material that frames the v1 plan and what comes
after it, but is **not part of the codebase**. Nothing here is executed or
imported. Two flavours live here: **backward-looking** original intent
(`DataQ_platform_roadmap.md`) and the **forward-looking** post-v1 planning input
(`post-v1-roadmap.md`) — both feed planning/task-generation, neither is kept in
sync with the code automatically.

**Authority:** where this folder and [`docs/adr/`](../docs/adr/README.md) disagree,
the **ADRs win** — they record decisions made *after* this material and
deliberately supersede parts of it (e.g. ADF/Airflow are orchestration providers,
not datasources; DQX is deferred to v1.1). Treat the roadmap here as the original
intent, not the current contract.

## Contents

| File | Purpose |
|---|---|
| `DataQ_platform_roadmap.md` | The original 8-week / 100-task product roadmap. Mirrored — with execution status — in [`docs/progress-v1.md`](../docs/progress-v1.md) (the archived v1 ledger; the live post-v1 tracker is [`docs/progress.md`](../docs/progress.md)). |
| `post-v1-roadmap.md` | The single home for **everything deferred past v1** — design themes (with pointers to the detailed design docs under [`docs/`](../docs/)) **and** the full `Backlog (post-v1 / testing)` GitHub-milestone issue list, mapped by theme. The intended **input for a post-v1 week-wise task generator**. Status lives on the GitHub milestone, not here. |

## Notes

- Internal Azure resource names have been replaced with neutral placeholders
  (e.g. `example-adf-preprod`) so the public repo doesn't expose real naming
  conventions. Substitute your own when provisioning.
