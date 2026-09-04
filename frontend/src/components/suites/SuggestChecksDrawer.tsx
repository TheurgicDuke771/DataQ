import { Alert, App, Button, Drawer, Empty, Flex, List, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';

import {
  type CheckSuggestion,
  type CheckSuggestionsResponse,
  runLlmFeature,
  suggestChecks,
} from '../../api/llm';
import { createCheck } from '../../api/suites';
import { suggestionToCheck } from './suggestions';
import { errorMessage, fetchFailure } from '../../utils/errors';
import { DimensionTag } from '../checks/checkBadges';

type SuggestState =
  | { status: 'running' }
  | { status: 'done'; result: CheckSuggestionsResponse }
  | { status: 'failed'; error: string }
  | { status: 'unavailable'; reason: string };

/**
 * Profiler-driven check suggestions (#1845, ADR 0042). Every suggestion already passed the
 * `create_check` validator server-side; nothing is created until the user adds it here.
 */
export function SuggestChecksDrawer({
  suiteId,
  open,
  onClose,
  onAdded,
}: {
  suiteId: string;
  open: boolean;
  onClose: () => void;
  /** Called after each successful add so the checks list refetches. */
  onAdded: () => void;
}) {
  return (
    <Drawer title="Suggested checks" open={open} onClose={onClose} size={640} destroyOnHidden>
      {open && <SuggestBody suiteId={suiteId} onAdded={onAdded} />}
    </Drawer>
  );
}

function SuggestBody({ suiteId, onAdded }: { suiteId: string; onAdded: () => void }) {
  const { message } = App.useApp();
  const [state, setState] = useState<SuggestState>({ status: 'running' });
  const [added, setAdded] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState<Set<number>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    (async () => {
      try {
        const row = await runLlmFeature<CheckSuggestionsResponse>(() => suggestChecks(suiteId), {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (row.status === 'succeeded' && row.response) {
          setState({ status: 'done', result: row.response });
        } else {
          setState({
            status: 'failed',
            error: row.error ?? 'the suggestion run failed without a reason',
          });
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        const failure = fetchFailure(err);
        if (failure.status === 409) setState({ status: 'unavailable', reason: failure.message });
        else setState({ status: 'failed', error: errorMessage(err) });
      }
    })();
    return () => controller.abort();
  }, [suiteId]);

  const add = async (index: number, s: CheckSuggestion) => {
    setAdding((prev) => new Set(prev).add(index));
    try {
      await createCheck(suiteId, suggestionToCheck(s));
      setAdded((prev) => new Set(prev).add(index));
      message.success(`${s.name}: added`);
      onAdded();
    } catch (err) {
      message.error(`Could not add “${s.name}”: ${errorMessage(err)}`);
    } finally {
      setAdding((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
    }
  };

  if (state.status === 'running') {
    return (
      <Flex vertical gap={8}>
        <Typography.Text>Profiling the target and asking the model…</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          The model sees column names and masked statistics — including each non-sensitive
          column&apos;s most frequent values — never rows. A local model can take a minute.
        </Typography.Text>
      </Flex>
    );
  }
  if (state.status === 'unavailable') {
    return (
      <Alert
        type="info"
        showIcon
        title="AI suggestions are not enabled on this workspace"
        description={state.reason}
      />
    );
  }
  if (state.status === 'failed') {
    return <Alert type="error" showIcon title="No suggestions" description={state.error} />;
  }

  const { suggestions, rejected, coverage_warnings: warnings } = state.result;
  const pending = suggestions.map((_, i) => i).filter((i) => !added.has(i));
  const addAll = async () => {
    for (const i of pending) await add(i, suggestions[i]);
  };

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Text type="secondary">
          {suggestions.length} suggestion{suggestions.length === 1 ? '' : 's'} passed validation.
          Nothing is created until you add it.
        </Typography.Text>
        <Button
          type="primary"
          size="small"
          onClick={addAll}
          disabled={pending.length === 0 || adding.size > 0}
        >
          Add all remaining
        </Button>
      </Flex>
      {suggestions.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No suggestion survived." />
      ) : (
        <List
          dataSource={suggestions.map((s, index) => ({ s, index }))}
          renderItem={({ s, index }) => (
            <List.Item
              actions={[
                added.has(index) ? (
                  <Tag key="added" color="success">
                    Added
                  </Tag>
                ) : (
                  <Button
                    key="add"
                    size="small"
                    loading={adding.has(index)}
                    onClick={() => add(index, s)}
                  >
                    Add
                  </Button>
                ),
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size={6} wrap>
                    <span>{s.name}</span>
                    <Tag>{s.expectation_type}</Tag>
                    <DimensionTag dimension={s.dimension} />
                  </Space>
                }
                description={
                  <Flex vertical gap={2}>
                    <span>{s.rationale}</span>
                    <Typography.Text type="secondary" code style={{ fontSize: 12 }}>
                      {JSON.stringify(s.config)}
                      {s.fail_threshold_hours != null ? ` · fail ≥ ${s.fail_threshold_hours}h` : ''}
                    </Typography.Text>
                  </Flex>
                }
              />
            </List.Item>
          )}
        />
      )}
      {rejected.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`${rejected.length} suggestion${rejected.length === 1 ? '' : 's'} rejected by the validator`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {rejected.map((r, i) => (
                <li key={i}>
                  {r.name ? <strong>{r.name}: </strong> : null}
                  {r.reason}
                </li>
              ))}
            </ul>
          }
        />
      )}
      {warnings.length > 0 && (
        <Alert
          type="info"
          showIcon
          title="A pipeline nearly triggers this suite"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {warnings.map((w, i) => (
                <li key={i}>
                  {w.provider} · {w.pipeline_or_dag_id} runs in {w.run_env ?? '?'} but the binding
                  is for {w.binding_env ?? '?'} — no freshness check was suggested.
                </li>
              ))}
            </ul>
          }
        />
      )}
    </Flex>
  );
}
