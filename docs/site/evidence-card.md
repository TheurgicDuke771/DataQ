# The incident evidence card

When a check breaches into a warning or failure severe enough to open or update an incident,
DataQ assembles a fixed, deterministic snapshot of everything relevant to that failure —
**before** any narrative is generated over it, not as part of generating one. This page is that
snapshot's contract: what each layer contains, what it is computed from, what a `null` in it
actually means, and the privacy guarantee it carries.

The card is deterministic on purpose. A narrative generated later (see
[MCP tool design: honesty & disclosure](mcp-honesty.md) for the same discipline applied to the
rest of the AI-facing surface) can only ever reason over this fixed bundle — it never goes back
to a live datasource, and every claim it makes must cite which layer below supports it. The
evidence is gathered the same way regardless of whether any narrative is ever requested over it.

## What it is a snapshot of

The card is captured at the moment an incident opens or re-occurs — it describes **that
occurrence**, not the current live state of the check, the asset, or the pipeline. A check
renamed since, or a table that has since recovered, still reads through the card as it was at
the time of the breach. Read it as "when this last failed," never as "right now."

## The layers

| Layer | Contains | Computed from |
|---|---|---|
| `check` | The failing check's id, name, expectation type, and monitor kind | The check row at capture time |
| `asset` | The table/file this check runs against | The resolved asset for the run |
| `failing_result` | Status, the numeric metric, the GX-shaped observed/expected values | The breaching result — **no failing sample rows**, see below |
| `kind_detail` | The kind-specific fields (e.g. `age_hours` for freshness, `z_score` for anomaly) pulled out of the raw result, so a reader doesn't need to know all four monitor-kind JSON shapes | `failing_result`'s own observed value, reshaped |
| `metric_trend` | The 10 most recent readings for this check, newest first | This check's own result history |
| `sibling_checks` | Every other check's outcome in the **same run** | The run the failing result belongs to |
| `same_asset_siblings` | The latest outcome of every *other* check targeting this **same asset**, across every suite, from the last 7 days | Every suite's results on this asset, not just this run's |
| `upstream_pipeline_run` | The orchestration pipeline run that triggered this suite run, its duration, and its delay against that pipeline's own recent history | The linked `pipeline_runs` row, when one triggered this run |
| `downstream_blast_radius` | The assets reachable downstream of this one via recorded lineage | The lineage graph, depth-capped |
| `profile_diff` | Always `null` | Not implemented — see below |

## The privacy guarantee

`failing_result` never carries a failing sample row — the specific record values that tripped
the check are deliberately excluded from the card at the point it is built, not filtered out
later. A narrative generated over this card, or a client reading it directly, can describe *that
a column failed a null check* but cannot see *which row*. Use the run's own results — which
apply the product's standard column-aware redaction — to inspect specific failing values.

`failing_result.observed_value` is a second, narrower door to real data: some expectation types
(min/max/mean-style rules) carry a literal warehouse cell value there, not just GX metadata. That
value is routed through the same column-policy/warehouse-tag redaction floor every other results
surface applies — but, unlike the live results endpoints, this card computes it **once**, at the
point the incident opens or gets a fresh occurrence, and stores the result. A suite's column
policy edited afterward does not retroactively re-mask an already-stored card.

`same_asset_siblings` is assembled once, for the workspace as a whole, with no caller in scope —
it has to be, since it draws from every suite that touches the asset, not just the one that
opened this incident. Reading it back applies a second, per-request narrowing: a caller only
sees the sibling entries on suites they hold at least view access to; entries from suites they
cannot see are withheld and folded into a count instead of being named. An outbound alert
narrows the same layer differently — to entries on the incident's own suite only, since an alert
channel has no per-viewer grant to check it against.

## Reading a `null` layer correctly

A `null` layer does not by itself mean something went wrong — each layer means a different thing
by it, and collapsing them into one "missing data" reading is the mistake this section exists to
prevent:

- **`upstream_pipeline_run` is `null` for the ordinary case** — a manually-triggered or
  scheduled run, which is most runs. It means no orchestration pipeline triggered this run, not
  that one should have and didn't.
- **`kind_detail` is `null` for an ordinary expectation or comparison check** — the common case,
  where the result's own observed value already is the shape a reader wants, so there is nothing
  to lift out. For a freshness, volume, schema-drift, or anomaly check, by contrast, the card is
  only ever captured from a genuinely warned, failed, or critical occurrence — never an
  operational error or a skip — so a `null` there for one of those four kinds means the same rare
  thing a missing `check` layer means: the check itself is gone.
- **`metric_trend`, `sibling_checks`, and `same_asset_siblings` are `[]`, not `null`, when there
  is genuinely nothing to show.** A `null` in one of these three specifically means the layer
  could not be built at all (an unexpected failure while assembling it) — distinct from an empty
  result, which means it was built successfully and found nothing.
- **`downstream_blast_radius` being `[]` has three different real causes that read identically**:
  the asset could not be resolved, the asset is a genuine lineage leaf, or the workspace has no
  lineage recorded at all. An empty list here is a floor, not proof that nothing is affected
  downstream.
- **`profile_diff` is always `null`.** This is a documented placeholder for a comparison DataQ
  does not yet compute (it would need a live datasource read of both the before and after batch,
  not just already-captured data) — not a failed attempt, and not specific to any one incident.

## The provenance rule a narrative inherits

When a narrative is generated over this card, every claim it makes must cite which of a closed,
fixed set of layer names it rests on: `failing_result`, `kind_detail`, `metric_trend`,
`sibling_checks`, `same_asset_siblings`, `upstream_pipeline_run`, `downstream_blast_radius`, and
`check_history` — a longer per-check result history fetched alongside the card, not one of the
card's own fields, since the card itself keeps only the last 10 points. `check`, `asset`, and the
always-null `profile_diff` are identifying context, not evidence a hypothesis can be pinned to,
so they are deliberately outside this set. A hypothesis that cannot point to a real layer in it
is refused before it can ever reach a reader. The generation step also computes, deterministically and
without ever asking the model, what the snapshot could not see — a withheld cross-suite sibling
count, an unresolved upstream pipeline, no recorded downstream lineage — because a model cannot
be trusted to notice or disclose a gap it was never shown existed in the first place.
