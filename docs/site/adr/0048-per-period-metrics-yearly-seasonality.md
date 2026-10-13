# ADR 0048 — Per-period metrics, yearly seasonality and seasonal periods on the anomaly monitor

- **Status:** Accepted (2026-10-13)
- **Date:** 2026-10-13
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (monitor kinds, `metric_value`), [0016](0016-severity-derivation-semantics.md) (severity bands), [0041](0041-history-audit-strategy.md) (audit), [0047](0047-automated-coverage-loop.md) (automatic baselines, which this does not extend).

## Context

The anomaly monitor compares a run against the check's own recent history: the last few runs,
optionally the same weekday only. It watches three things, all of them whole-table: the row
count, the age of the newest timestamp, and the column profile. It keeps eight weeks of
observations at most.

That is the wrong shape for a table with a yearly rhythm. A sales table has sale periods. Its
whole-table row count only grows, so it says nothing about them. And if DataQ watched sales
per day instead, the last few weeks would be the wrong comparison twice over: every recurring
peak would look like an anomaly, and a peak that should have arrived and did not (a load that
failed during the sale) would look normal.

Two things are missing: a metric that is a value *per period*, and a baseline that knows what
this time of year usually looks like.

## Decision

1. **Two per-period metrics on the anomaly kind.** `period_row_count` counts the rows whose
   date falls in a period; `period_sum` sums a numeric column over them. The check names a
   `date_column`, a `period` (`day` or `week`) and, for the sum, a `value_column`. A run
   measures **complete** periods only, in UTC, with one pushdown aggregate over a half-open
   range of the date column. The verdict is about the last complete period.

   An observation is keyed by its period, not by when the run happened, so a second run in
   the same period replaces the value and does not add a point. Each run re-reads the trailing
   seven periods in the same query and updates them, because rows arrive late; only the last
   one is judged.

   These are authored checks on SQL datasources. They are **not** added to automatic coverage
   (ADR 0047): an automatic baseline assumes nothing about a table, and choosing which column
   is the business date and which is the amount is a claim a person makes.

2. **History is seeded once, then accumulated.** On the run that finds no baseline, one
   grouped query reads up to three years of complete periods from the table's own rows and
   stores them marked `backfilled`. Every later run adds what it measured, marked `observed`.
   The two are kept apart because they are different evidence: an observed point is what the
   table held when DataQ looked, and a backfilled point is what the table says today about
   the past, which a restatement or a purge can have changed. Each result reports how many of
   the points it was scored against were backfilled.

   Seeding is one scan of the table. It can be switched off on the check (`backfill: false`)
   for a warehouse that bills per query or a table whose old rows are archived; the check then
   starts empty and waits. Dropping a check's baseline seeds it again.

3. **Yearly seasonality is a seasonal index over the rolling baseline.** With `yearly: true`:

   - The *level* at a period is the median of the `window` periods before it.
   - The *index* for a period is what the same period in an earlier year was, divided by the
     level then. A day is matched to the same ISO week and weekday a year back; a week to the
     same ISO week. With more than one earlier year the index is their median.
   - The run's value and each prior value are divided by their index, and the ordinary
     z-score is taken over those. A sale that ran at 2.4 times its surrounding weeks last year
     is expected at 2.4 times now, so arriving at 2.4 times is normal and arriving flat is not.

   Every number in that chain is in the result: the level, the index, the years it came from,
   and the expected value. A period with no earlier year to match scores with an index of 1,
   which is exactly today's behaviour, and the result says the yearly comparison was not
   applied and why. Nothing is skipped for want of a year: the check is useful from the day it
   has `min_points`, and becomes seasonal when the history allows.

   Yearly seasonality is offered on the per-period metrics only. Weekday seasonality stays as
   it is and can be combined with it.

4. **Seasonal periods are declared by people and detected from history, and both are used
   only to judge the data.**

   - **Declared.** A workspace calendar of named periods, each either recurring (a month and
     day range, every year) or dated (explicit start and end, for a sale that moves). Admins
     manage it; changes are audited. It is workspace-wide because a sale period is a fact
     about the business, not about one check.
   - **Detected.** Read from a check's own history when asked: a run of periods at or above
     1.5 times the level. Seen at the same time in two or more years it is *recurring*; in one
     year, *seen once*. Detected periods are shown on the check's trend and can be promoted to
     the calendar by an admin. They are never stored as fact on their own.
   - **Effect.** Inside a declared period, a day is matched to the same offset within that
     period's previous occurrence, not to the same ISO week, which is what makes a moving
     sale line up. Periods inside a declared or recurring period are left out of the level, so
     the weeks after a sale are not judged against the sale. And a period inside a declared
     period with no earlier occurrence in the history is reported as `skip`, with that reason:
     DataQ has been told a peak is expected and has nothing to say how large.

5. **Retention.** A per-period check keeps three years of periods plus its window (about
   1,100 daily points), whatever its seasonality switches say. Other anomaly checks keep what
   they keep today.

## Phases

1. Per-period metrics: measurement, validation, observations keyed by period.
2. Seeding from the table and three-year retention.
3. Yearly seasonality in scoring.
4. The declared calendar (schema first, on its own).
5. Detected periods and the read surfaces (REST, MCP).
6. Check editor, trend view and calendar UI; user docs.

## Consequences

- A recurring peak stops paging people, and a missing one starts to.
- The first yearly verdict needs a year of history. Seeding supplies it where the table still
  holds its past; where it does not, the check waits a year, and says so on every result
  until then.
- One earlier year cannot tell a recurring sale from a one-off. The index uses it anyway,
  because one year is better than none, and the result names how many years stand behind it.
- A backfilled baseline trusts the table's history. Marking the points is what keeps that
  visible.
- Three years of daily points is a larger baseline row than any check has had. It is one JSONB
  value per check and is read once per run.

## Considered and rejected

- **A report of when the sales are**, for analysts to plan from: that is business analysis,
  and a BI tool is the place for it. DataQ shows the periods it detected because they explain
  its verdicts, not as a product of their own.
- **Accumulating only:** no yearly verdict for a year, and two before a period can be called
  recurring, on tables that already hold the history.
- **Backfilling only:** nothing would record what the table held when each run looked, so a
  later restatement would silently rewrite the baseline.
- **Year-over-year on the whole-table metrics:** a growing table's row count has no seasons.
- **Comparing only against the same period in earlier years:** two or three points give no
  spread to score against. The index keeps the recent window as the source of spread.
- **A forecasting library** (seasonal decomposition or similar): a new dependency whose
  verdict cannot be explained from the numbers in the result.
- **Declared periods on each check:** the same sale would be typed into every check that
  watches a table it touches.
- **Adding these checks to automatic coverage:** see decision 1. Automatic coverage may later
  *suggest* one for review.
