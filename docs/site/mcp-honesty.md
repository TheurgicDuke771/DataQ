# MCP tool design: honesty & disclosure

A REST caller wrote their own query and reads it back through a UI with a row count, a
timestamp, a "running" badge, and the filter they themselves chose. An AI client calling
DataQ's MCP tools has none of that surrounding context — only the fields a tool call returns
and the docstring the model read before deciding to call it. A response that is **literally
true** while silently omitting what it cannot see produces a **confident wrong answer**, which
is worse than an explicit error: nothing in it signals the model to doubt it.

This page documents the design discipline every tool on DataQ's MCP surface is built and
reviewed against. It is also the contract another MCP client or integration can rely on: any
tool that violates one of these rules is a defect, not an accepted limitation.

## The six audit criteria

Every tool is checked against six questions before it ships, and again whenever a change
touches it:

1. **Population.** Does an empty or short result mean "nothing exists," or could it mean
   "nothing matched a scope the caller can't see" — an unmonitored asset, a caller with no
   share on the relevant suite, a filter that silently matched zero rows?
2. **Time window.** Is there an implicit window — a default lookback, a page size — that a
   reader could mistake for "everything," rather than "everything within a bound this response
   should also state"?
3. **Truncation.** Is a truncated page distinguishable from a complete one using a real,
   computed total, rather than inferred from the page happening to be shorter than the limit?
4. **Freshness.** Could a value be read as "current" when it is actually a stored reading from
   an earlier point — or, more importantly, when it has never been read at all?
5. **Null/zero semantics.** Does a null or a zero mean exactly one thing? The failure mode is a
   single value standing in for two different real states — most often "checked, and the
   answer is none/zero" collapsed together with "never checked."
6. **Adjacent blindness.** Does the tool say what a *neighboring* fact it cannot see might also
   be true — a trigger binding existing is not the same fact as that binding ever having fired;
   a stored classification is not the same fact as a live probe having just confirmed it.

A tool clean on five of six and silent on the last one still produces a confidently wrong
answer on exactly the question that criterion covers — the audit is pass/fail per criterion,
not scored.

## Docstring shape: summary, then returns, then caveats

Every tool's docstring follows the same order, and the order is deliberate — a caveat placed
*before* the description of what the tool returns makes the tool harder to select correctly
without making the caveat any more likely to be read once the tool is already chosen:

1. **One line of summary** — what question this tool answers, in the caller's terms ("find
   orchestration triggers that are silently never firing"), not an implementation description.
2. **What it returns**, field by field where the shape isn't obvious, so a model can select
   between two similar-looking tools correctly.
3. **Caveats last** — the population/window/truncation/freshness/null/adjacency limits from the
   six criteria above, stated as facts about the response, not hedges about the tool.

A limit that changes what a caller must do — not just what they should keep in mind — is a
**field**, not a paragraph: prose is easy to skip, a field a well-behaved client must branch on
is not. `truncated`, `results_final`, and `redacted_columns` are three examples of limits that
graduated from a caveat sentence into a field of their own once it became clear a model was
expected to act differently depending on the answer.

## Safety annotations, derived from one registry

Every tool carries exactly one of three annotations, and all three are derived from the same
per-tool authorization registry that also enforces access control — so the documentation and
the enforcement cannot silently drift apart the way a hand-maintained description of "which
tools are safe" would.

- **Read-only.** No state changes, nothing spent. The majority of the surface.
- **State-changing.** Creates, updates, deletes, or triggers something — gated on edit-level
  access to the resource it acts on.
- **Live-probe.** Persists nothing in DataQ's own storage, but opens a live connection to a
  remote system using a stored credential — a real cost and a real side effect on that remote
  system even though nothing is saved here. These are gated exactly like state-changing tools,
  not like reads, because "nothing was written to our database" is not the same guarantee as
  "nothing happened."

No tool requires the highest workspace-admin privilege tier. Every admin-only capability in
DataQ's authorization model is a datasource connection mutation (create, update, delete,
re-authenticate) — and none of those are exposed to MCP at all, on the standing principle that
a stored credential must never transit an LLM.

## Standing rules

Three shapes recur often enough to name directly, because naming a failure mode is what makes
it checkable in review rather than something each tool re-discovers on its own:

- **Partial-as-final.** A result that is genuinely in progress must never be described in terms
  that read as a finished verdict. "3 of 3 checks run so far passed" is a true and dangerously
  incomplete answer to "did the suite pass" when the suite has 30 checks; the response states
  whether the run is actually finished, not just what has completed so far.
- **Unknown-as-zero.** A count, a duration, or a streak that has never been measured is not the
  same fact as one that was measured and came back zero. Rendering "never measured" as `0`
  reads as reassurance where the honest answer is silence — a null and a documented reason for
  it, not a number that happens to look calm.
- **One null, two meanings.** The same missing value can mean structurally different things —
  "this kind of thing has no reading to give" versus "a reading exists in principle and nothing
  has gone and gotten it yet." Collapsing both into one null erases the difference between a
  ceiling and a gap; where both are possible, they are two separate fields.

## What this buys a client author

None of the above requires trusting DataQ's own model calls — the discipline lives at the tool
layer, independent of whether any AI feature is enabled. A client built against this contract
can rely on: a truncated page always being labeled as such against a real total, a partial run
never posing as a finished one, and a tool's own docstring stating plainly what it cannot see
rather than leaving that to be discovered from a wrong answer.
