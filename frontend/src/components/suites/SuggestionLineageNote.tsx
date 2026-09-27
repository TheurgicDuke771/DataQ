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
        An equivalent check already runs upstream on the same column (
        {[where, hidden].filter(Boolean).join('; ')}). A pass-through copy may not need its own —
        but joins and filters can still break it, so decide per table.{partialNote}
      </Typography.Text>
    );
  }
  if (lineage.recommendation === 'place_at_origin') {
    const origin = (lineage.origins ?? []).find((o) => o.pass_through);
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="lineage-note">
        This column is passed through unchanged from{' '}
        {origin ? `${tableName(origin.asset_name)}.${origin.column}` : 'an upstream table'}.
        Checking it there as well fires once per bad load, not once per copy.{partialNote}
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
  return null;
}

function tableName(name: string | null): string {
  if (!name) return 'unknown asset';
  const parts = name.split('.');
  return parts[parts.length - 1] ?? name;
}
