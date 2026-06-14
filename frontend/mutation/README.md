# Frontend mutation testing (Stryker)

The frontend analogue of the backend mutmut spike ([CONTRIBUTING rule 4a](../../CONTRIBUTING.md)).
**Manual / periodic, never CI** — mutation testing is too slow to gate merges, and
its big tool tree shouldn't sit on the `pnpm audit` surface. So, like the backend's
standalone `requirements-mutation.txt`, **Stryker is not a dependency in
`frontend/package.json`** — [`run.sh`](./run.sh) installs it ad-hoc (pinned) and
restores the manifest on exit.

## Run a spike

```bash
frontend/mutation/run.sh                                  # mutate the configured target
frontend/mutation/run.sh --mutate 'src/components/checks/checkForm.ts'
```

Config lives in [`../stryker.conf.json`](../stryker.conf.json); the version pin is in
`run.sh`. The default `mutate` target is `suiteTarget.ts` — point it at whatever pure
module you're hardening (the conversion/resolver utils are the best targets:
`suiteTarget.ts`, `checkForm.ts`, `expectationCatalog.ts`, `resultsFormat.ts`).

## Reading the result

Stryker prints a `[Survived]` block per mutant the tests failed to kill, plus a
mutation-score table. Triage each survivor:

- **Real gap** — a behavioural mutation (flipped condition, dropped branch, changed
  return) that no test caught. Add/strengthen a test. This is the point of the spike.
- **Equivalent mutant** — a change with no observable effect (e.g. mutating a value
  that's re-narrowed downstream, or an unreachable branch). Not a gap; leave it.
- **Low-value** — e.g. an error-`message` string mutated to `""`. Pin it only if the
  copy is contractual; asserting exact UI strings is usually brittle.

A score < 100% is fine — chase the _real_ gaps, not the number.

## Gotchas (Node ≥ 20, pnpm v11)

- Stryker must run from `frontend/` — it sandboxes the cwd, so a separate manifest
  dir can't host it. `run.sh` cd's there for you.
- `stryker.conf.json` sets `plugins: ["@stryker-mutator/vitest-runner"]` explicitly;
  pnpm's strict `node_modules` layout defeats Stryker's default `@stryker-mutator/*`
  plugin-discovery glob.
- `pnpm dlx` is unreliable here (its isolated store can't resolve Stryker's dynamic
  `typescript` import), which is why `run.sh` does a real install + revert instead.
