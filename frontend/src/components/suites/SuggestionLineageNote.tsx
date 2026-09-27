import { Typography } from 'antd';

import type { SuggestionLineage } from '../../api/llm';

/**
 * Column-lineage advice on one suggested check — placement and dedup. Advice only: a lineage
 * pair records derivation, not equality, so the suggestion is never hidden or dropped.
 */
export function SuggestionLineageNote({ lineage }: { lineage?: SuggestionLineage }) {
  if (!lineage) return null;
  if (lineage.upstream_status === 'error') {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Column lineage could not be checked for this suggestion.
      </Typography.Text>
    );
  }
  const partial = lineage.complete === false;
  const partialNote = partial
    ? ' Lineage is incomplete here, so something further upstream may apply too.'
    : '';
  if (lineage.recommendation === 'already_covered_upstream') {
    const named = lineage.equivalent_upstream_checks ?? [];
    const restricted = lineage.restricted_equivalent_checks ?? 0;
    const where = named.map((c) => `${tableName(c.asset_name)}.${c.column}`).join(', ');
    const hidden = restricted > 0 ? `${restricted} in suites you can't view` : '';
    return (
      <Typography.Text type="warning" style={{ fontSize: 12 }} data-testid="lineage-note">
        The same check, with the same parameters, already runs upstream on a column of the same name
        ({[where, hidden].filter(Boolean).join('; ')}). If this column is a plain copy it may not
        need its own — but joins, filters and aggregation can still break it, so decide per table.
        {partialNote}
      </Typography.Text>
    );
  }
  if (lineage.recommendation === 'place_at_origin') {
    const origin = (lineage.origins ?? []).find((o) => o.pass_through);
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="lineage-note">
        A column of the same name upstream feeds this one (
        {origin ? `${tableName(origin.asset_name)}.${origin.column}` : 'an upstream table'}).
        Checking it there as well fires once per bad load rather than once per copy.{partialNote}
      </Typography.Text>
    );
  }
  const derived = (lineage.origins ?? []).filter((o) => !o.pass_through);
  if (derived.length > 0) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="lineage-note">
        Derived from {derived.map((o) => `${tableName(o.asset_name)}.${o.column}`).join(', ')} — not
        a plain copy, so an upstream check would not cover it.{partialNote}
      </Typography.Text>
    );
  }
  if (partial || lineage.upstream_status === 'incomplete') {
    // Could-not-follow is a different fact from nothing-recorded — say it (#828).
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="lineage-note">
        Column lineage is incomplete for this column, so where it comes from is unknown.
      </Typography.Text>
    );
  }
  return null;
}

function tableName(name: string | null): string {
  if (!name) return 'unknown asset';
  const parts = name.split('.');
  return parts[parts.length - 1] ?? name;
}
