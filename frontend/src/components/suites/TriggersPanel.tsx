import { DeleteOutlined, WarningOutlined } from '@ant-design/icons';
import {
  App,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Select,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import SimpleList from '../SimpleList';
import { useState } from 'react';

import { CONNECTION_ENVS, type ConnectionEnv, ENV_COLORS, envLabel } from '../../api/connections';
import { listSuites, type Suite } from '../../api/suites';
import {
  createTriggerBinding,
  deleteTriggerBinding,
  listEnvNearMisses,
  listTriggerBindings,
  PROVIDER_LABELS,
  setTriggerBindingEnabled,
  SUITE_TRIGGER_PROVIDER,
  type TriggerBinding,
  type TriggerEnvNearMiss,
  TRIGGER_PROVIDERS,
  type TriggerProvider,
} from '../../api/triggerBindings';
import { useAsyncData } from '../../hooks/useAsyncData';
import { AsyncBody } from '../AsyncBody';
import { formatTimestamp } from '../results/resultsFormat';
import { errorMessage } from '../../utils/errors';

/**
 * Surface any #1186 advisory warnings a create/enable response carried — e.g. "this connection's
 * URL is also configured on another env's connection.
 */
function warnAboutBinding(
  message: ReturnType<typeof App.useApp>['message'],
  binding: TriggerBinding,
): void {
  for (const warning of binding.warnings) {
    message.warning(warning.message, 8);
  }
}

/**
 * Suite-detail panel for the suite's run triggers: bind an orchestrator pipeline/ DAG so the suite
 * runs on that pipeline's *success* (CLAUDE.md §4.
 */
export function TriggersPanel({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  const { state, reload } = useAsyncData(() => listTriggerBindings(suiteId));
  // Best-effort (#1199): the currently-active #1186 env near-misses for this suite's bindings.
  const { state: nearMissState, reload: reloadNearMisses } = useAsyncData(() =>
    listEnvNearMisses(suiteId),
  );
  const nearMisses = nearMissState.status === 'ok' ? nearMissState.data : [];
  // The suites the user can see: names for suite-on-suite rows and options for the picker.
  const { state: suitesState } = useAsyncData((signal) => listSuites(undefined, signal));
  const suites = suitesState.status === 'ok' ? suitesState.data : [];

  // Every mutation (add / enable-toggle / delete) invalidates BOTH reads: the near-miss candidate
  // set is re-derived server-side from the ENABLED bindings.
  const onChanged = () => {
    reload();
    reloadNearMisses();
  };

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Triggers</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Run this suite when an orchestrator pipeline / DAG completes successfully, or when
            another suite passes.
          </Typography.Text>
        </Flex>
      }
    >
      <Flex vertical gap={8}>
        {/* An outage must never be reportable as a state (the ADR-0039 lesson):
            with no notice, a failed near-miss fetch renders identically to "no
            mismatch", so a live #1186 mismatch would read as healthy. Say so
            instead — non-blocking, below the bindings' own error handling. */}
        {nearMissState.status === 'error' && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Env-mismatch warnings are unavailable right now — a binding may have one that isn&apos;t
            shown.
          </Typography.Text>
        )}
        <TriggersBody
          state={state}
          suiteId={suiteId}
          canManage={canManage}
          onChanged={onChanged}
          nearMisses={nearMisses}
          suites={suites}
        />
      </Flex>
    </Card>
  );
}

function TriggersBody({
  state,
  suiteId,
  canManage,
  onChanged,
  nearMisses,
  suites,
}: {
  state: ReturnType<typeof useAsyncData<TriggerBinding[]>>['state'];
  suiteId: string;
  canManage: boolean;
  onChanged: () => void;
  nearMisses: TriggerEnvNearMiss[];
  suites: Suite[];
}) {
  const suiteNames = new Map(suites.map((s) => [s.id, s.name]));
  return (
    <AsyncBody state={state} loadingText="Loading triggers…" errorTitle="Failed to load triggers">
      {(bindings) => (
        <Flex vertical gap={16}>
          {canManage && (
            <AddTrigger
              suiteId={suiteId}
              onAdded={onChanged}
              upstreamOptions={suites.filter((s) => s.id !== suiteId)}
            />
          )}
          {bindings.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="No triggers — this suite runs only on manual / scheduled runs."
            />
          ) : (
            <SimpleList
              dataSource={bindings}
              renderItem={(binding) => (
                <TriggerRow
                  key={binding.id}
                  binding={binding}
                  canManage={canManage}
                  onChanged={onChanged}
                  upstreamName={
                    binding.provider === SUITE_TRIGGER_PROVIDER
                      ? suiteNames.get(binding.pipeline_or_dag_id)
                      : undefined
                  }
                  // `filter`, not `find` (#1199 review): one binding can have several
                  // simultaneously-current near-misses.
                  nearMisses={nearMisses.filter(
                    (nm) =>
                      nm.provider === binding.provider &&
                      nm.pipeline_or_dag_id === binding.pipeline_or_dag_id &&
                      nm.binding_env === binding.env,
                  )}
                />
              )}
            />
          )}
        </Flex>
      )}
    </AsyncBody>
  );
}

function TriggerRow({
  binding,
  canManage,
  onChanged,
  nearMisses,
  upstreamName,
}: {
  binding: TriggerBinding;
  canManage: boolean;
  onChanged: () => void;
  /** For a suite-on-suite binding: the upstream suite's name, when the user can see it. */
  upstreamName?: string;
  /**
   * Every #1186 env near-miss currently observed for this exact binding (#1199) — runs keep
   * landing in each entry's `run_env`, not this binding's `env`.
   */
  nearMisses: TriggerEnvNearMiss[];
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  // A suite binding's key is a suite id: show the name; fall back to the id only when the
  // upstream is outside the user's access (it still fires — they just cannot open it).
  const shown =
    binding.provider === SUITE_TRIGGER_PROVIDER
      ? (upstreamName ?? binding.pipeline_or_dag_id)
      : binding.pipeline_or_dag_id;

  const onToggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      const updated = await setTriggerBindingEnabled(binding.id, enabled);
      message.success(`${shown}: ${enabled ? 'enabled' : 'disabled'}`);
      warnAboutBinding(message, updated);
      onChanged();
    } catch (err) {
      message.error(`Update failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async () => {
    setBusy(true);
    try {
      await deleteTriggerBinding(binding.id);
      message.success(`${shown}: removed`);
      onChanged();
    } catch (err) {
      message.error(`Remove failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SimpleList.Item
      actions={
        canManage
          ? [
              <Switch
                key="toggle"
                size="small"
                checked={binding.enabled}
                loading={busy}
                onChange={onToggle}
                aria-label={`Enable ${shown}`}
              />,
              <Button
                key="remove"
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                loading={busy}
                onClick={onRemove}
                aria-label={`Remove ${shown}`}
              />,
            ]
          : [<Tag key="state">{binding.enabled ? 'enabled' : 'disabled'}</Tag>]
      }
    >
      <Flex gap={10} align="center" style={{ minWidth: 0 }}>
        <Tag color={ENV_COLORS[binding.env as ConnectionEnv]}>
          {envLabel(binding.env as ConnectionEnv)}
        </Tag>
        <Flex vertical gap={2} style={{ minWidth: 0 }}>
          <Typography.Text code={binding.provider !== SUITE_TRIGGER_PROVIDER} ellipsis>
            {shown}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {PROVIDER_LABELS[binding.provider]}
          </Typography.Text>
        </Flex>
        {nearMisses.map((nearMiss) => (
          <Tooltip
            key={nearMiss.run_env}
            // The claim is past-tense and dated, not an unqualified present-tense assertion: the
            // row only proves the mismatch was OBSERVED.
            title={`Last observed ${formatTimestamp(nearMiss.updated_at)}: a run landed in "${envLabel(nearMiss.run_env as ConnectionEnv)}", not "${envLabel(nearMiss.binding_env as ConnectionEnv)}" — this binding did not fire, and won't for such runs until the envs match.`}
          >
            <Tag
              color="warning"
              icon={<WarningOutlined />}
              aria-label={`Env mismatch near-miss: ${nearMiss.run_env}`}
            >
              env mismatch: {envLabel(nearMiss.run_env as ConnectionEnv)}
            </Tag>
          </Tooltip>
        ))}
      </Flex>
    </SimpleList.Item>
  );
}

function AddTrigger({
  suiteId,
  onAdded,
  upstreamOptions,
}: {
  suiteId: string;
  onAdded: () => void;
  /** Suites the user can see, minus this one — the picker for "Another suite (on success)". */
  upstreamOptions: Suite[];
}) {
  const { message } = App.useApp();
  const [provider, setProvider] = useState<TriggerProvider>();
  const [env, setEnv] = useState<ConnectionEnv>();
  const [pipelineId, setPipelineId] = useState('');
  const [upstreamId, setUpstreamId] = useState<string>();
  const [adding, setAdding] = useState(false);
  const isSuite = provider === SUITE_TRIGGER_PROVIDER;
  const ready = isSuite ? Boolean(upstreamId) : Boolean(provider && env && pipelineId.trim());

  const onAdd = async () => {
    if (!provider || !ready) return;
    const id = isSuite ? (upstreamId ?? '') : pipelineId.trim();
    setAdding(true);
    try {
      // A suite binding takes the upstream suite's env server-side; nothing to send.
      const created = await createTriggerBinding({
        provider,
        ...(isSuite ? {} : { env }),
        pipeline_or_dag_id: id,
        suite_id: suiteId,
      });
      const named = isSuite ? (upstreamOptions.find((s) => s.id === id)?.name ?? id) : id;
      message.success(`${named}: trigger added`);
      warnAboutBinding(message, created);
      setProvider(undefined);
      setEnv(undefined);
      setPipelineId('');
      setUpstreamId(undefined);
      onAdded();
    } catch (err) {
      message.error(`Add failed: ${errorMessage(err)}`);
    } finally {
      setAdding(false);
    }
  };

  return (
    <Flex gap={8} align="center" wrap>
      <Select
        value={provider}
        onChange={setProvider}
        placeholder="Provider"
        style={{ width: 210 }}
        options={TRIGGER_PROVIDERS.map((p) => ({ value: p, label: PROVIDER_LABELS[p] }))}
        aria-label="Provider"
      />
      {isSuite ? (
        <Select
          value={upstreamId}
          onChange={setUpstreamId}
          showSearch={{ optionFilterProp: 'label' }}
          placeholder="Upstream suite"
          aria-label="Upstream suite"
          style={{ flex: 1, minWidth: 200 }}
          options={upstreamOptions.map((s) => ({ value: s.id, label: s.name }))}
          notFoundContent="No other suite you can see"
        />
      ) : (
        <>
          <Input
            value={pipelineId}
            onChange={(e) => setPipelineId(e.target.value)}
            placeholder="Pipeline / DAG id"
            aria-label="Pipeline / DAG id"
            style={{ flex: 1, minWidth: 160 }}
            onPressEnter={onAdd}
          />
          <Select
            value={env}
            onChange={setEnv}
            placeholder="Env"
            style={{ width: 100 }}
            options={CONNECTION_ENVS.map((e) => ({ value: e, label: envLabel(e) }))}
            aria-label="Env"
          />
        </>
      )}
      <Button type="primary" loading={adding} disabled={!ready} onClick={onAdd}>
        Add
      </Button>
    </Flex>
  );
}
