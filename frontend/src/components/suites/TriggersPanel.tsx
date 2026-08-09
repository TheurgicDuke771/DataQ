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
import {
  createTriggerBinding,
  deleteTriggerBinding,
  listEnvNearMisses,
  listTriggerBindings,
  ORCHESTRATION_PROVIDERS,
  type OrchestrationProvider,
  PROVIDER_LABELS,
  setTriggerBindingEnabled,
  type TriggerBinding,
  type TriggerEnvNearMiss,
} from '../../api/triggerBindings';
import { useAsyncData } from '../../hooks/useAsyncData';
import { AsyncBody } from '../AsyncBody';
import { errorMessage } from '../../utils/errors';

/**
 * Surface any #1186 advisory warnings a create/enable response carried — e.g.
 * "this connection's URL is also configured on another env's connection, so a
 * run there won't match this binding." Non-blocking: the binding was already
 * saved successfully, this is purely informational (antd `message.warning`,
 * longer duration than the success toast so it's actually readable).
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
 * Suite-detail panel for the suite's run triggers: bind an orchestrator pipeline/
 * DAG so the suite runs on that pipeline's *success* (CLAUDE.md §4 — orchestration
 * providers are never a datasource; this is the one place a pipeline id meets a
 * suite). Anyone with `view` sees the bindings; `edit`+ (`canManage`) gets the
 * add / enable-toggle / remove controls, matching the backend gate.
 */
export function TriggersPanel({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  const { state, reload } = useAsyncData(() => listTriggerBindings(suiteId));
  // Best-effort (#1199): the currently-active #1186 env near-misses for this
  // suite's bindings. Fetched separately from the bindings list — a failure here
  // must never block the bindings themselves from rendering, so only the 'ok'
  // case is used; loading/error silently render no badges.
  const { state: nearMissState, reload: reloadNearMisses } = useAsyncData(() =>
    listEnvNearMisses(suiteId),
  );
  const nearMisses = nearMissState.status === 'ok' ? nearMissState.data : [];

  // Every mutation (add / enable-toggle / delete) invalidates BOTH reads: the
  // near-miss candidate set is re-derived server-side from the ENABLED bindings,
  // so disabling or deleting the mismatched binding resolves its near-miss. Only
  // reloading the bindings would leave a warning badge sitting on a binding the
  // user just switched off until the panel remounted.
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
            Run this suite when an orchestrator pipeline / DAG completes successfully.
          </Typography.Text>
        </Flex>
      }
    >
      <TriggersBody
        state={state}
        suiteId={suiteId}
        canManage={canManage}
        onChanged={onChanged}
        nearMisses={nearMisses}
      />
    </Card>
  );
}

function TriggersBody({
  state,
  suiteId,
  canManage,
  onChanged,
  nearMisses,
}: {
  state: ReturnType<typeof useAsyncData<TriggerBinding[]>>['state'];
  suiteId: string;
  canManage: boolean;
  onChanged: () => void;
  nearMisses: TriggerEnvNearMiss[];
}) {
  return (
    <AsyncBody state={state} loadingText="Loading triggers…" errorTitle="Failed to load triggers">
      {(bindings) => (
        <Flex vertical gap={16}>
          {canManage && <AddTrigger suiteId={suiteId} onAdded={onChanged} />}
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
                  // `filter`, not `find` (#1199 review): one binding can have
                  // several simultaneously-current near-misses — the same DAG id
                  // reported by two orchestrator connections in two different
                  // wrong envs is literally the #1186 ambiguity this feature
                  // exists to catch, and showing only the first would hide a live
                  // mismatch behind another live one.
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
}: {
  binding: TriggerBinding;
  canManage: boolean;
  onChanged: () => void;
  /** Every #1186 env near-miss currently observed for this exact binding (#1199)
   *  — runs keep landing in each entry's `run_env`, not this binding's `env`. A
   *  binding can have more than one at a time (one per wrong env observed), and
   *  each gets its own badge so a second live mismatch is never hidden. */
  nearMisses: TriggerEnvNearMiss[];
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);

  const onToggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      const updated = await setTriggerBindingEnabled(binding.id, enabled);
      message.success(`${binding.pipeline_or_dag_id}: ${enabled ? 'enabled' : 'disabled'}`);
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
      message.success(`${binding.pipeline_or_dag_id}: removed`);
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
                aria-label={`Enable ${binding.pipeline_or_dag_id}`}
              />,
              <Button
                key="remove"
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                loading={busy}
                onClick={onRemove}
                aria-label={`Remove ${binding.pipeline_or_dag_id}`}
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
          <Typography.Text code ellipsis>
            {binding.pipeline_or_dag_id}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {PROVIDER_LABELS[binding.provider]}
          </Typography.Text>
        </Flex>
        {nearMisses.map((nearMiss) => (
          <Tooltip
            key={nearMiss.run_env}
            title={`Runs keep landing in "${envLabel(nearMiss.run_env as ConnectionEnv)}", not "${envLabel(nearMiss.binding_env as ConnectionEnv)}" — this binding has not fired and won't until the env matches (#1186).`}
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

function AddTrigger({ suiteId, onAdded }: { suiteId: string; onAdded: () => void }) {
  const { message } = App.useApp();
  const [provider, setProvider] = useState<OrchestrationProvider>();
  const [env, setEnv] = useState<ConnectionEnv>();
  const [pipelineId, setPipelineId] = useState('');
  const [adding, setAdding] = useState(false);

  const onAdd = async () => {
    const id = pipelineId.trim();
    if (!provider || !env || !id) return;
    setAdding(true);
    try {
      const created = await createTriggerBinding({
        provider,
        env,
        pipeline_or_dag_id: id,
        suite_id: suiteId,
      });
      message.success(`${id}: trigger added`);
      warnAboutBinding(message, created);
      setProvider(undefined);
      setEnv(undefined);
      setPipelineId('');
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
        style={{ width: 170 }}
        options={ORCHESTRATION_PROVIDERS.map((p) => ({ value: p, label: PROVIDER_LABELS[p] }))}
        aria-label="Provider"
      />
      <Input
        value={pipelineId}
        onChange={(e) => setPipelineId(e.target.value)}
        placeholder="Pipeline / DAG id"
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
      <Button
        type="primary"
        loading={adding}
        disabled={!provider || !env || !pipelineId.trim()}
        onClick={onAdd}
      >
        Add
      </Button>
    </Flex>
  );
}
