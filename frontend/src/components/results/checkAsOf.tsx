import { Tooltip, Typography } from 'antd';
import type { ReactNode } from 'react';

import type { Result, ResultCheck } from '../../api/runs';
import type { Check } from '../../api/suites';

/**
 * A result's check identity AS OF that result (#1613), with how far to trust it.
 *
 * `version`: a `check_versions` snapshot in effect when the result was written — the engine,
 * dimension and type that actually applied. `later_version`: every snapshot postdates the
 * result (clock skew), so the API handed back the earliest one. `live`: the check's CURRENT
 * row stands in — either the API said so (`resolved_from: 'live'`, no version history) or the
 * payload carries no snapshot at all (an older API, a recorded capture) and only the suite's
 * live check list is available. The last two may differ from what evaluated the result, so a
 * cell must say so rather than present them as history.
 */
export type CheckAsOfSource = 'version' | 'later_version' | 'live';

export interface CheckAsOf {
  name: string | null;
  kind: string | null;
  engine: string;
  expectation_type: string | null;
  dimension: string | null;
  source: CheckAsOfSource;
}

const NOTE: Record<Exclude<CheckAsOfSource, 'version'>, string> = {
  live: "Shown from the check's current state — no snapshot of the check as it was at this result is available (it has no version history, or this run was loaded without one). It may have changed since the run.",
  later_version:
    "Shown from the check's earliest recorded version, which postdates this result — the check's state at the run was not recorded and may have differed.",
};

const MARKER: Record<Exclude<CheckAsOfSource, 'version'>, string> = {
  live: '(current)',
  later_version: '(later)',
};

/** Resolve a result's check as-of; `null` when neither the result nor the live list knows it. */
// eslint-disable-next-line react-refresh/only-export-components -- helpers + the cell belong together (checkBadges precedent)
export function checkAsOf(result: Result, live: Check | undefined): CheckAsOf | null {
  const snapshot: ResultCheck | null | undefined = result.check;
  if (snapshot) {
    return {
      name: snapshot.name,
      kind: snapshot.kind,
      engine: snapshot.engine,
      expectation_type: snapshot.expectation_type,
      dimension: snapshot.dimension,
      source:
        snapshot.resolved_from === 'version' || snapshot.resolved_from === 'later_version'
          ? snapshot.resolved_from
          : 'live',
    };
  }
  if (!live) return null;
  return {
    name: live.name,
    kind: live.kind,
    engine: live.engine || 'gx',
    expectation_type: live.expectation_type,
    dimension: live.dimension ?? null,
    source: 'live',
  };
}

/** Plain-text marker for a cell or export value that is NOT a genuine as-of snapshot; empty
 *  for one that is. */
// eslint-disable-next-line react-refresh/only-export-components -- helpers + the cell belong together (checkBadges precedent)
export function currentSuffix(source: CheckAsOfSource | undefined): string {
  return source && source !== 'version' ? ` ${MARKER[source]}` : '';
}

/** The machine-readable column for exports: `version` | `later_version` | `current` | ''. */
// eslint-disable-next-line react-refresh/only-export-components -- helpers + the cell belong together (checkBadges precedent)
export function exportSource(source: CheckAsOfSource | undefined): string {
  if (!source) return '';
  return source === 'live' ? 'current' : source;
}

/**
 * Wraps a badge or text with the honesty marker when the value is not a snapshot in effect at
 * the result — a muted "(current)" / "(later)" with the explanation in a tooltip and the
 * accessible name, so a reader never mistakes today's engine or dimension for the one that
 * produced the result.
 */
export function AsOfCell({
  source,
  children,
}: {
  source: CheckAsOfSource | undefined;
  children: ReactNode;
}) {
  if (!source || source === 'version') return <>{children}</>;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {children}
      <Tooltip title={NOTE[source]}>
        <Typography.Text type="secondary" style={{ fontSize: 11 }} aria-label={NOTE[source]}>
          {MARKER[source]}
        </Typography.Text>
      </Tooltip>
    </span>
  );
}
