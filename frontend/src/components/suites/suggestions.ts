import type { CheckSuggestion } from '../../api/llm';
import type { CheckCreate } from '../../api/suites';

const FRESHNESS_TYPE = 'monitor:freshness';

/** A suggestion → the same `CheckCreate` the editor would submit for it. */
export function suggestionToCheck(s: CheckSuggestion): CheckCreate {
  const isFreshness = s.expectation_type === FRESHNESS_TYPE;
  return {
    name: s.name,
    kind: isFreshness ? 'freshness' : 'expectation',
    expectation_type: s.expectation_type,
    config: s.config,
    dimension: s.dimension ?? undefined,
    fail_threshold: isFreshness ? (s.fail_threshold_hours ?? null) : null,
  };
}
