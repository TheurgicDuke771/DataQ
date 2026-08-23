import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  listEnvNearMisses,
  listTriggerBindings,
  type TriggerBinding,
} from '../../src/api/triggerBindings';
import { TriggersPanel } from '../../src/components/suites/TriggersPanel';

/**
 * The "near-miss fetch fails without blocking the bindings list" case (#1199), split into its own
 * file deliberately.
 */

vi.mock('../../src/api/triggerBindings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/triggerBindings')>();
  return {
    ...actual,
    listTriggerBindings: vi.fn(),
    listEnvNearMisses: vi.fn(),
  };
});

const mockList = vi.mocked(listTriggerBindings);
const mockNearMisses = vi.mocked(listEnvNearMisses);

const BINDING: TriggerBinding = {
  id: 'b1',
  provider: 'adf',
  pipeline_or_dag_id: 'nightly-load',
  env: 'prod',
  suite_id: 's1',
  enabled: true,
  warnings: [],
};

afterEach(() => vi.clearAllMocks());

describe('TriggersPanel — near-miss fetch resilience (#1199)', () => {
  // Deliberately ONE `it`, asserting the whole failure contract in a single render — the file
  // docstring above explains why a second one cannot live here.
  it('keeps the bindings list and says the warnings are unavailable, rather than looking clean', async () => {
    mockList.mockResolvedValue([BINDING]);
    mockNearMisses.mockRejectedValue(new Error('network error'));

    render(
      <AntApp>
        <TriggersPanel suiteId="s1" canManage />
      </AntApp>,
    );

    // The bindings themselves still render — the near-miss fetch is an overlay.
    expect(await screen.findByText('nightly-load')).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Env mismatch near-miss/)).not.toBeInTheDocument();
    // …but an outage must never be reportable as a state (the ADR-0039 lesson): silent zero badges
    // look exactly like "no mismatch".
    expect(await screen.findByText(/Env-mismatch warnings are unavailable/)).toBeInTheDocument();
  });
});
