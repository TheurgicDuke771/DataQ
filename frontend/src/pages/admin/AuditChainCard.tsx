import { Alert, Button, Descriptions, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';

import { type AuditChainStatus, verifyAuditChain } from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { type FetchFailure, fetchFailure } from '../../utils/errors';
import { Section } from './parts';

/** Verification walks the whole hashed set, so it is never run on mount — the
 *  card opens in this state and stays here until the admin asks for it. */
type VerifyState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'done'; result: AuditChainStatus; checkedAt: string }
  | { status: 'failed'; failure: FetchFailure };

const STATUS_TAG: Record<AuditChainStatus['status'], { color: string; label: string }> = {
  ok: { color: 'green', label: 'Intact' },
  broken: { color: 'red', label: 'Broken' },
  // Not "intact": no hashed row has been written yet, so nothing was checked.
  empty: { color: 'default', label: 'Nothing to verify' },
};

/** Tamper-evidence status for the append-only audit log (ADR 0041 §9). */
export function AuditChainCard() {
  const [state, setState] = useState<VerifyState>({ status: 'idle' });

  const verify = async () => {
    setState({ status: 'running' });
    try {
      const result = await verifyAuditChain();
      setState({ status: 'done', result, checkedAt: new Date().toISOString() });
    } catch (err) {
      // A failed verification is never rendered as an intact chain — the one
      // question this card answers is the one where a wrong answer is worst.
      setState({ status: 'failed', failure: fetchFailure(err) });
    }
  };

  return (
    <Section title="Audit chain">
      <Typography.Text type="secondary">
        Every audit event is hashed over the one before it, so a row that was edited or removed
        breaks the chain. Verification reads the whole hashed set into memory and may take a while
        on a large log, so it only runs when you ask for it.
      </Typography.Text>

      <Flex align="center" gap={12} wrap>
        <Button type="primary" onClick={verify} loading={state.status === 'running'}>
          Verify now
        </Button>
        {state.status === 'idle' && <Tag>Not verified this session</Tag>}
        {state.status === 'done' && (
          <>
            <Tag color={STATUS_TAG[state.result.status].color}>
              {STATUS_TAG[state.result.status].label}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Checked {formatTimestamp(state.checkedAt)}
            </Typography.Text>
          </>
        )}
        {state.status === 'failed' && <Tag color="orange">Not verified — the check failed</Tag>}
      </Flex>

      {state.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          title="Could not verify the audit chain"
          description={
            <Flex vertical gap={4}>
              <span>
                {state.failure.message} — this says nothing about whether the chain is intact, only
                that the check did not complete.
              </span>
              {state.failure.requestId && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Request ID: <Typography.Text code>{state.failure.requestId}</Typography.Text>
                </Typography.Text>
              )}
            </Flex>
          }
        />
      )}

      {state.status === 'done' && <ChainResult result={state.result} />}
    </Section>
  );
}

function ChainResult({ result }: { result: AuditChainStatus }) {
  return (
    <Flex vertical gap={12}>
      {result.status === 'broken' && result.first_break && (
        <Alert
          type="error"
          showIcon
          title="The audit chain is broken"
          description={
            <Flex vertical gap={4}>
              <span>
                First break at event{' '}
                <Typography.Text code>{result.first_break.event_id}</Typography.Text>
                {result.first_break.occurred_at
                  ? `, recorded ${formatTimestamp(result.first_break.occurred_at)}`
                  : ' (its timestamp could not be read)'}
                . Events written after this point cannot be shown to be untampered.
              </span>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Expected previous hash{' '}
                <Typography.Text code>
                  {result.first_break.expected_prev_hash ?? '—'}
                </Typography.Text>
                , found{' '}
                <Typography.Text code>{result.first_break.actual_prev_hash ?? '—'}</Typography.Text>
              </Typography.Text>
            </Flex>
          }
        />
      )}
      {result.status === 'empty' && (
        <Alert
          type="info"
          showIcon
          title="No hashed events yet"
          description="Nothing has been written to the chain, so there is nothing to verify. This is not the same as a verified-intact log."
        />
      )}

      <Descriptions column={1} size="small">
        <Descriptions.Item label="Events in chain">{result.verified_count}</Descriptions.Item>
        <Descriptions.Item label="Not covered by the chain">
          {result.unverifiable_legacy_count === 0 ? (
            <Typography.Text type="secondary">None</Typography.Text>
          ) : (
            `${result.unverifiable_legacy_count} event(s) written before the chain shipped — real history, just not hash-covered`
          )}
        </Descriptions.Item>
        <Descriptions.Item label="Chain head">
          {result.chain_head_hash ? (
            <Typography.Text code copyable={{ text: result.chain_head_hash }}>
              {result.chain_head_hash.slice(0, 16)}…
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">None</Typography.Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="External anchor">
          {result.anchor_mode === 'webhook' ? (
            <Tag color="green">Anchored off-box</Tag>
          ) : (
            <Flex vertical gap={2}>
              <Tag>Not configured</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Without an anchor the chain is only internally consistent — anyone able to rewrite
                the whole table could also rewrite the hashes.
              </Typography.Text>
            </Flex>
          )}
        </Descriptions.Item>
      </Descriptions>
    </Flex>
  );
}
