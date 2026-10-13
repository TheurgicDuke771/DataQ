# ADR 0048 — Per-period metrics, yearly seasonality and seasonal periods on the anomaly monitor

- **Status:** Accepted (2026-10-13)
- **Date:** 2026-10-13
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0012](0012-monitor-kind-seam.md) (monitor kinds, `metric_value`), [0016](0016-severity-derivation-semantics.md) (severity bands), [0041](0041-history-audit-strategy.md) (audit), [0047](0047-automated-coverage-loop.md) (automatic baselines, which this does not extend).

## Context

The anomaly monitor is the one place in DataQ with a learned baseline: every other monitor
kind (freshness, volume, aggregate, schema drift) compares against a fixed threshold or the
previous run. It scores a run against the check's own recent history: the last `window` runs,
or the last `window` runs on the same weekday. It keeps `window` observations, seven times
that with weekday seasonality: weeks or months of history, never years. And it watches three
things, all whole-table: the row count, the age of the newest timestamp, and the column
profile.

That is the wrong shape for data with a yearly rhythm. A sales table has sale periods. Its
whole-table row count only grows, so it says nothing about them. And if DataQ watched sales
per day instead, the last few weeks would be the wrong comparison twice over: every recurring
peak would look like an anomaly, and a peak that should have arrived and did not (a load that
failed during the sale) would look normal.

Two things are missing: a metric that is a value *per period*, and a baseline that knows what
this time of year usually looks like.

## Decision

1. **Two per-period metrics on the anomaly kind.** `period_row_count` counts the rows whose
   date falls in a period; `period_sum` sums a numeric column over them. The check names a
   `date_column`, a `period` (`day` or `week`) and, for the sum, a `value_column`.

   - **Buckets** are calendar dates of the column as the warehouse returns it (the column
     cast to a date). A column with no time zone is taken at face value. A zone-aware
     timestamp is bucketed in the warehouse session's zone, which DataQ does not set; the
     editor says so.
   - **Which periods are complete** is decided in the check's `timezone` (an IANA name,
     default UTC), and a period is judged only once it has been closed for `settle_hours`
     (default 6), so a suite scheduled just after midnight does not judge yesterday before
     the nightly load has landed.
   - **One point per period, written once.** The verdict is about the most recent settled
     period. A second run before the next period settles measures it again for the result
     but does not add or change a stored point. Rows that arrive after a period was judged
     are not reflected in its point; that is the price of a point meaning "what the table
     held when DataQ judged it".

   These are authored checks on SQL datasources.

2. **History is seeded once, then accumulated.** On the run that finds no baseline, one
   grouped query reads up to three years of periods from the table's own rows. Every period
   before the one being judged is stored marked `backfilled`; the judged one, and every one
   after it, is `observed`. The mark never changes. The two are kept apart because they are
   different evidence: an observed point is what the table held when DataQ looked, and a
   backfilled point is what the table says today about the past, which a restatement or a
   purge can have changed. Each result reports how many of the points it was scored against
   were backfilled.

   Seeding is one scan of the table. It can be switched off on the check (`backfill: false`)
   for a warehouse that bills per query or a table whose old rows are archived; the check then
   starts empty and waits. Dropping a check's baseline seeds it again.

3. **Yearly seasonality is a seasonal index over the rolling baseline.** With `yearly: true`:

   - The *level* at a period is the median of the `window` periods before it.
   - The *index* for a period is what the matching period in an earlier year was, divided by
     the level then. With more than one earlier year the index is their median.
   - *Matching.* A day is matched to the same weekday nearest the same calendar date in the
     earlier year (52 weeks back, then 104, and so on, corrected to the nearest such
     weekday); a week to the week containing that date. There is no "week 53" case. A peak
     tied to a fixed date (25 December) or to a moving one (a sale) does not line up this
     way, and needs a declared period (decision 4).
   - *Scoring.* The expected value is the level now times the index. The run is scored on its
     distance from that, against the spread of the same distance over the prior periods
     (each prior's value less its own expected value). Nothing is divided by the index, so
     an index of zero (the shop was closed that day last year) is an ordinary case: zero is
     expected. A sale that ran at 2.4 times its surrounding weeks last year is expected at
     2.4 times now, so arriving at 2.4 times is normal and arriving flat is not.
   - *When it does not apply.* The index is used only where it means something: the level
     then was above zero and neither it nor the matched value is negative. Otherwise, and
     for a period with no earlier year to match, the index is 1, which is exactly today's
     behaviour, and the result says the yearly comparison was not applied and why (no
     earlier year, a zero level, a negative sum).

   Every number in that chain is in the result: the level, the index, the years it came from
   and the expected value. Nothing is skipped for want of a year: the check is useful from
   the day it has `min_points`, and becomes seasonal when the history allows.

4. **Yearly seasonality is a property of the anomaly kind, not of one metric.** It is
   offered on every metric that is one number per run: the two per-period metrics, the
   whole-table row count and the freshness age. For the whole-table metrics a "period" is a
   calendar day, the stored point for a day is the last run's value that day, and history
   can only accumulate: there is nothing to seed a past row count from. Weekday seasonality
   stays as it is and combines with it. The column profile does not get it: it is hundreds
   of series per check, and three years of each is not a baseline, it is a warehouse.

   Automatic coverage (ADR 0047) gets it through the setting it already has: the baseline
   shape gains a `yearly` switch, default off, which applies to the automatic row-count and
   freshness baselines. The per-period metrics are **not** added to automatic coverage: an
   automatic baseline assumes nothing about a table, and choosing which column is the
   business date and which is the amount is a claim a person makes. Automatic coverage may
   later *suggest* one for review.

5. **Seasonal periods are declared by people and detected from history. Only declared ones
   change a verdict.**

   - **Declared.** A workspace calendar of named periods, each either recurring (a month and
     day range, every year) or dated (explicit start and end, for a sale that moves). Admins
     manage it; changes are audited. It is workspace-wide because a sale period is a fact
     about the business. A check names the calendar entries that apply to it, and none do by
     default, so adding an entry changes no check until someone attaches it.
   - **What a declared period does.** A day inside it is matched to the same offset within
     that period's previous occurrence, which is what makes a moving sale or a fixed date
     line up. Periods inside it are left out of the level, so the weeks after a sale are not
     judged against the sale. And when the period has no earlier occurrence in the history,
     the check is told a rise is expected and cannot say how large: a value **above** what
     the rolling baseline expects is reported as within the declared period and not banded,
     while a value **below** it is judged as usual. A load that fails during a first-year
     sale still alerts.
   - **Detected.** Read from a check's own history when someone asks: a run of periods at or
     above 1.5 times the level. Seen at the same time in two or more years it is *recurring*;
     in one year, *seen once*. Detected periods are shown on the check's trend so a person
     can see why a verdict came out as it did, and an admin can promote one to the calendar.
     Until then a detected period is a description: it is not stored and it changes no
     score. The seasonal index already carries a recurring peak into the expected value
     without it.

6. **Retention.** A check with `yearly` on, or on a per-period metric, keeps three years of
   periods plus its window (about 1,100 daily points). Other anomaly checks keep what they
   keep today.

## Phases

1. Per-period metrics: measurement, validation, one point per settled period.
2. Seeding from the table and three-year retention.
3. Yearly seasonality in scoring, for every single-number metric.
4. The declared calendar (schema first, on its own), and attaching entries to a check.
5. Detected periods and the read surfaces (REST, MCP).
6. Check editor, trend view and calendar UI; user docs.
7. The `yearly` switch in the automatic-coverage baseline shape, with an amendment to
   ADR 0047.

## Consequences

- A recurring peak stops paging people, and a missing one starts to.
- The first yearly verdict needs a year of history. Seeding supplies it for a per-period
  metric where the table still holds its past. Everywhere else the check waits a year, and
  says so on every result until then.
- One earlier year cannot tell a recurring sale from a one-off. The index uses it anyway,
  because one year is better than none, and the result names how many years stand behind it.
- A backfilled baseline trusts the table's history. Marking the points is what keeps that
  visible.
- Three years of daily points is a larger baseline row than any check has had. It is one JSONB
  value per check and is read once per run. Switching `yearly` on for automatic coverage
  multiplies that by every covered table, which is why it is off by default there.
- Late rows do not revise a judged period. A table that fills in slowly needs a longer
  `settle_hours`, not a re-read.

## Considered and rejected

- **A report of when the sales are**, for analysts to plan from: that is business analysis,
  and a BI tool is the place for it. DataQ shows the periods it detected because they explain
  its verdicts, not as a product of their own.
- **Accumulating only:** no yearly verdict for a year, and two before a period can be called
  recurring, on tables that already hold the history.
- **Backfilling only:** nothing would record what the table held when each run looked, so a
  later restatement would silently rewrite the baseline.
- **Re-reading recent periods on every run** to pick up late rows: it rewrites observed
  points after they were judged, which is the same objection.
- **Yearly seasonality on the per-period metrics alone:** a snapshot table that is replaced
  every night has a row count with seasons too, and the automatic baselines would never get
  it.
- **Comparing only against the same period in earlier years:** two or three points give no
  spread to score against. The index keeps the recent window as the source of spread.
- **Matching by ISO week number:** some years have a week 53 and the earlier year does not.
- **Skipping every verdict inside a declared period with no history:** it would hide a failed
  load during a sale, which is the incident this exists to catch.
- **Letting a detected period change the baseline before a person confirms it:** an
  unreviewed guess would move verdicts with nothing in the result to say so.
- **A calendar entry that applies to every check automatically:** one admin entry would
  change the verdicts of checks on tables the sale never touches.
- **A forecasting library** (seasonal decomposition or similar): a new dependency whose
  verdict cannot be explained from the numbers in the result.
- **Declared periods typed into each check:** the same sale would be entered again for every
  table it touches. A check attaches entries; it does not define them.
