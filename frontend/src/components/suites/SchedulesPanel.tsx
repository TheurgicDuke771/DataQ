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
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';

import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  type Schedule,
  timezoneOptions,
  updateSchedule,
} from '../../api/schedules';
import { useAsyncData } from '../../hooks/useAsyncData';
import { formatTimestamp } from '../results/resultsFormat';

/**
 * Suite-detail panel for cron-driven run schedules (A7). A schedule runs the
 * suite unattended on a 5-field cron cadence in an IANA timezone — distinct from
 * Triggers (run-on-pipeline-success). Anyone with `view` sees the schedules;
 * `edit`+ (`canManage`) gets the create / pause-toggle / delete controls, matching
 * the backend gate. Cron/timezone are validated server-side (422 → inline error).
 */
export function SchedulesPanel({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  const { state, reload } = useAsyncData(() => listSchedules(suiteId));

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Schedules</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Run this suite automatically on a cron cadence.
          </Typography.Text>
        </Flex>
      }
    >
      <SchedulesBody state={state} suiteId={suiteId} canManage={canManage} onChanged={reload} />
    </Card>
  );
}

function SchedulesBody({
  state,
  suiteId,
  canManage,
  onChanged,
}: {
  state: ReturnType<typeof useAsyncData<Schedule[]>>['state'];
  suiteId: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  if (state.status === 'loading') {
    return <Spin tip="Loading schedules…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert type="error" showIcon message="Failed to load schedules" description={state.error} />
    );
  }
  const schedules = state.data;

  return (
    <Flex vertical gap={16}>
      {canManage && <AddSchedule suiteId={suiteId} onAdded={onChanged} />}
      {schedules.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="No schedules — this suite runs only on manual / triggered runs."
        />
      ) : (
        <ScheduleTable schedules={schedules} canManage={canManage} onChanged={onChanged} />
      )}
    </Flex>
  );
}

function ScheduleTable({
  schedules,
  canManage,
  onChanged,
}: {
  schedules: Schedule[];
  canManage: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [busyId, setBusyId] = useState<string | null>(null);

  const onToggle = async (s: Schedule, enabled: boolean) => {
    setBusyId(s.id);
    try {
      await updateSchedule(s.id, { enabled });
      message.success(`${s.cron}: ${enabled ? 'resumed' : 'paused'}`);
      onChanged();
    } catch (err) {
      message.error(`Update failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setBusyId(null);
    }
  };

  const onRemove = async (s: Schedule) => {
    setBusyId(s.id);
    try {
      await deleteSchedule(s.id);
      message.success(`${s.cron}: removed`);
      onChanged();
    } catch (err) {
      message.error(`Remove failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setBusyId(null);
    }
  };

  const columns: ColumnsType<Schedule> = [
    {
      title: 'Cron',
      dataIndex: 'cron',
      render: (cron: string) => <Typography.Text code>{cron}</Typography.Text>,
    },
    { title: 'Timezone', dataIndex: 'timezone', width: 160 },
    {
      title: 'Next run',
      dataIndex: 'next_run_at',
      render: (t: string) => formatTimestamp(t),
    },
    {
      title: 'Last run',
      dataIndex: 'last_run_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Status',
      dataIndex: 'enabled',
      width: 100,
      render: (enabled: boolean, s) =>
        canManage ? (
          <Switch
            size="small"
            checked={enabled}
            loading={busyId === s.id}
            onChange={(next) => onToggle(s, next)}
            aria-label={`${enabled ? 'Pause' : 'Resume'} ${s.cron}`}
          />
        ) : (
          <Tag color={enabled ? 'success' : 'default'}>{enabled ? 'enabled' : 'paused'}</Tag>
        ),
    },
    ...(canManage
      ? [
          {
            title: '',
            key: 'actions',
            width: 48,
            render: (_: unknown, s: Schedule) => (
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                loading={busyId === s.id}
                onClick={() => onRemove(s)}
                aria-label={`Remove ${s.cron}`}
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <Table<Schedule>
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={schedules}
      pagination={false}
    />
  );
}

function AddSchedule({ suiteId, onAdded }: { suiteId: string; onAdded: () => void }) {
  const { message } = App.useApp();
  const [cron, setCron] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [adding, setAdding] = useState(false);
  const zones = useMemo(() => timezoneOptions(), []);

  const onAdd = async () => {
    const expr = cron.trim();
    if (!expr) return;
    setAdding(true);
    try {
      await createSchedule({ suite_id: suiteId, cron: expr, timezone });
      message.success(`${expr}: scheduled`);
      setCron('');
      onAdded();
    } catch (err) {
      message.error(`Add failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setAdding(false);
    }
  };

  return (
    <Flex gap={8} align="center" wrap>
      <Input
        value={cron}
        onChange={(e) => setCron(e.target.value)}
        placeholder="Cron e.g. 0 9 * * 1-5 (min hour dom mon dow)"
        style={{ flex: 1, minWidth: 220 }}
        onPressEnter={onAdd}
        aria-label="Cron expression"
      />
      <Select
        showSearch
        value={timezone}
        onChange={setTimezone}
        style={{ width: 220 }}
        options={zones.map((z) => ({ value: z, label: z }))}
        aria-label="Timezone"
      />
      <Button type="primary" loading={adding} disabled={!cron.trim()} onClick={onAdd}>
        Add
      </Button>
    </Flex>
  );
}
