import { DeleteOutlined } from '@ant-design/icons';
import {
  App,
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Select,
  Spin,
  Switch,
  Tag,
  Typography,
} from 'antd';
import SimpleList from '../SimpleList';
import { useState } from 'react';

import { CONNECTION_ENVS, type ConnectionEnv, ENV_COLORS, envLabel } from '../../api/connections';
import {
  createTriggerBinding,
  deleteTriggerBinding,
  listTriggerBindings,
  ORCHESTRATION_PROVIDERS,
  type OrchestrationProvider,
  PROVIDER_LABELS,
  setTriggerBindingEnabled,
  type TriggerBinding,
} from '../../api/triggerBindings';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';

/**
 * Suite-detail panel for the suite's run triggers: bind an orchestrator pipeline/
 * DAG so the suite runs on that pipeline's *success* (CLAUDE.md §4 — orchestration
 * providers are never a datasource; this is the one place a pipeline id meets a
 * suite). Anyone with `view` sees the bindings; `edit`+ (`canManage`) gets the
 * add / enable-toggle / remove controls, matching the backend gate.
 */
export function TriggersPanel({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  const { state, reload } = useAsyncData(() => listTriggerBindings(suiteId));

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
      <TriggersBody state={state} suiteId={suiteId} canManage={canManage} onChanged={reload} />
    </Card>
  );
}

function TriggersBody({
  state,
  suiteId,
  canManage,
  onChanged,
}: {
  state: ReturnType<typeof useAsyncData<TriggerBinding[]>>['state'];
  suiteId: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  if (state.status === 'loading') {
    return <Spin description="Loading triggers…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load triggers" description={state.error} />
    );
  }
  const bindings = state.data;

  return (
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
            />
          )}
        />
      )}
    </Flex>
  );
}

function TriggerRow({
  binding,
  canManage,
  onChanged,
}: {
  binding: TriggerBinding;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);

  const onToggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      await setTriggerBindingEnabled(binding.id, enabled);
      message.success(`${binding.pipeline_or_dag_id}: ${enabled ? 'enabled' : 'disabled'}`);
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
      await createTriggerBinding({ provider, env, pipeline_or_dag_id: id, suite_id: suiteId });
      message.success(`${id}: trigger added`);
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
