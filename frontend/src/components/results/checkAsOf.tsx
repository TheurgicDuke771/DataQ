import { Tooltip, Typography } from 'antd';
import type { ReactNode } from 'react';

import type { Result, ResultCheck } from '../../api/runs';
import type { Check } from '../../api/suites';

/**
 * A result's check identity AS OF that result (#1613), with where it came from.
 *
 * `version`: a `check_versions` snapshot in effect when the result was written — the engine,
 * dimension and type that actually applied. `live`: the check's CURRENT row, because either the
 * API said so (`resolved_from: 'live'`, no version history) or the payload predates the field and
 * only the suite's live check list is available — so the values may postdate the run, and the
 * cell must say so rather than present them as history.
 */
export type CheckAsOfSource = 'version' | 'live';

export interface CheckAsOf {
  name: string | null;
  kind: string | null;
  engine: string;
  expectation_type: string | null;
  dimension: string | null;
  source: CheckAsOfSource;
}

export const CHECK_AS_OF_LIVE_NOTE =
  "Current value — this result has no version history, so the check's present state is shown; it may have changed since the run.";

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
      source: snapshot.resolved_from === 'version' ? 'version' : 'live',
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

/** Print-friendly marker for a `live`-sourced cell; empty for a real snapshot. */
// eslint-disable-next-line react-refresh/only-export-components -- helpers + the cell belong together (checkBadges precedent)
export function currentSuffix(source: CheckAsOfSource | undefined): string {
  return source === 'live' ? ' (current)' : '';
}

/**
 * Wraps a badge or text with the honesty marker when the value is the check's CURRENT state
 * rather than a snapshot — a muted "(current)" with the explanation in a tooltip, so a reader
 * never mistakes today's engine or dimension for the one that produced the result.
 */
export function AsOfCell({
  source,
  children,
}: {
  source: CheckAsOfSource | undefined;
  children: ReactNode;
}) {
  if (source !== 'live') return <>{children}</>;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {children}
      <Tooltip title={CHECK_AS_OF_LIVE_NOTE}>
        <Typography.Text
          type="secondary"
          style={{ fontSize: 11 }}
          aria-label={CHECK_AS_OF_LIVE_NOTE}
        >
          (current)
        </Typography.Text>
      </Tooltip>
    </span>
  );
}
