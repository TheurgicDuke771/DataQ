import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type AttachedCheck,
  createSeasonalPeriod,
  deleteSeasonalPeriod,
  describeSpan,
  listSeasonalPeriods,
  MONTH_NAMES,
  type SeasonalPeriod,
  type SeasonalPeriodKind,
  type SeasonalPeriodWrite,
  updateSeasonalPeriod,
} from '../../api/seasonalPeriods';
import { AsyncBody } from '../../components/AsyncBody';
import { Modal } from '../../components/shared/Modal';
import { useAppModal } from '../../hooks/useAppModal';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';
import { apiFieldError } from '../../utils/fieldErrors';

/** Admin → Calendar: the workspace's declared seasonal periods (sales, holidays). */
export function AdminCalendar() {
  const { state, reload } = useAsyncData(listSeasonalPeriods);
  const [modal, setModal] = useState<{ period?: SeasonalPeriod } | null>(null);
  const [blocked, setBlocked] = useState<{ name: string; checks: AttachedCheck[] } | null>(null);

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Seasonal calendar</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Named periods when the business runs differently, such as a sale or a holiday.
          </Typography.Text>
        </Flex>
      }
    >
      <Flex vertical gap={12}>
        <Alert
          type="info"
          showIcon
          title="An entry changes no check until it is attached"
          description="Attach entries in a check’s editor: on an anomaly check that watches rows or a sum per period, with yearly seasonality on. A day inside an attached period is compared with the same day of the period’s previous occurrence, and the period is left out of the level the days after it are judged against."
        />
        {blocked && (
          <Alert
            type="warning"
            showIcon
            closable={{ onClose: () => setBlocked(null) }}
            data-testid="calendar-delete-blocked"
            title={`“${blocked.name}” is still attached to ${blocked.checks.length} check${blocked.checks.length === 1 ? '' : 's'}`}
            description={
              <Flex vertical gap={4}>
                <span>Remove it from these checks first, then delete it:</span>
                <ul style={{ margin: 0, paddingInlineStart: 20 }}>
                  {blocked.checks.map((c) => (
                    <li key={c.id}>
                      <Link to={`/suites/${c.suite_id}/checks/${c.id}/edit`}>{c.name}</Link>{' '}
                      <Typography.Text type="secondary">in {c.suite_name}</Typography.Text>
                    </li>
                  ))}
                </ul>
              </Flex>
            }
          />
        )}
        <AsyncBody
          state={state}
          loadingText="Loading the calendar…"
          errorTitle="Failed to load the seasonal calendar"
        >
          {(periods) => (
            <Flex vertical gap={12}>
              <Flex justify="flex-end">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({})}>
                  New entry
                </Button>
              </Flex>
              {periods.length === 0 ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="No seasonal periods declared."
                />
              ) : (
                <PeriodTable
                  periods={periods}
                  onEdit={(period) => setModal({ period })}
                  onDeleted={() => {
                    setBlocked(null);
                    reload();
                  }}
                  onBlocked={setBlocked}
                />
              )}
            </Flex>
          )}
        </AsyncBody>
      </Flex>
      {modal !== null && (
        <PeriodFormModal
          key={modal.period?.id ?? 'new'}
          period={modal.period}
          onClose={() => setModal(null)}
          onSaved={() => {
            setModal(null);
            reload();
          }}
        />
      )}
    </Card>
  );
}

function PeriodTable({
  periods,
  onEdit,
  onDeleted,
  onBlocked,
}: {
  periods: SeasonalPeriod[];
  onEdit: (p: SeasonalPeriod) => void;
  onDeleted: () => void;
  onBlocked: (b: { name: string; checks: AttachedCheck[] }) => void;
}) {
  const { message } = App.useApp();
  const modal = useAppModal();

  const onDelete = (p: SeasonalPeriod) =>
    modal.confirm({
      title: `Delete “${p.name}”?`,
      content:
        p.attached_checks > 0
          ? `It is attached to ${p.attached_checks} check${p.attached_checks === 1 ? '' : 's'}; the delete is refused until it is removed from them.`
          : 'No check uses it.',
      okText: 'Delete',
      okType: 'danger',
      onOk: async () => {
        try {
          await deleteSeasonalPeriod(p.id);
          message.success(`${p.name} deleted`);
          onDeleted();
        } catch (err) {
          const api = apiFieldError(err);
          const checks = api?.detail.checks;
          if (api?.code === 'seasonal_period_in_use' && Array.isArray(checks)) {
            onBlocked({ name: p.name, checks: checks as AttachedCheck[] });
            return;
          }
          message.error(`Delete failed: ${errorMessage(err)}`);
          throw err;
        }
      },
    });

  const columns: ColumnsType<SeasonalPeriod> = [
    { title: 'Name', dataIndex: 'name' },
    {
      title: 'Kind',
      dataIndex: 'kind',
      render: (k: SeasonalPeriodKind) => <Tag>{k === 'recurring' ? 'Every year' : 'Dated'}</Tag>,
    },
    { title: 'When', key: 'when', render: (_, p) => describeSpan(p) },
    {
      title: 'Series',
      dataIndex: 'series',
      render: (s: string | null) => s ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    { title: 'Attached checks', dataIndex: 'attached_checks' },
    {
      title: 'Note',
      dataIndex: 'note',
      render: (n: string | null) => n ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: <span className="dq-sr-only">Actions</span>,
      key: 'actions',
      width: 80,
      render: (_, p) => (
        <Flex gap={4}>
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            onClick={() => onEdit(p)}
            aria-label={`Edit ${p.name}`}
          />
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={() => onDelete(p)}
            aria-label={`Delete ${p.name}`}
          />
        </Flex>
      ),
    },
  ];

  return (
    <Table<SeasonalPeriod>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={periods}
      pagination={false}
    />
  );
}

const MONTH_OPTIONS = MONTH_NAMES.map((label, i) => ({ value: i + 1, label }));

interface FormValues {
  name: string;
  kind: SeasonalPeriodKind;
  start_month?: number;
  start_day?: number;
  end_month?: number;
  end_day?: number;
  start_date?: string;
  end_date?: string;
  series?: string;
  note?: string;
}

/** The days a month can hold in some year (29 February is allowed). */
function daysIn(month: number | undefined): number {
  return month ? new Date(Date.UTC(2024, month, 0)).getUTCDate() : 31;
}

/** The whole entry as the API takes it: the other kind's fields are sent empty, so a kind change
 *  on edit does not leave the old kind's bounds behind. */
function toWrite(values: FormValues): SeasonalPeriodWrite {
  const recurring = values.kind === 'recurring';
  const text = (v: string | undefined) => (v?.trim() ? v.trim() : null);
  return {
    name: values.name.trim(),
    kind: values.kind,
    start_month: recurring ? (values.start_month ?? null) : null,
    start_day: recurring ? (values.start_day ?? null) : null,
    end_month: recurring ? (values.end_month ?? null) : null,
    end_day: recurring ? (values.end_day ?? null) : null,
    start_date: recurring ? null : (values.start_date ?? null),
    end_date: recurring ? null : (values.end_date ?? null),
    series: recurring ? null : text(values.series),
    note: text(values.note),
  };
}

function PeriodFormModal({
  period,
  onClose,
  onSaved,
}: {
  period?: SeasonalPeriod;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);
  const kind = Form.useWatch('kind', form) ?? period?.kind ?? 'recurring';
  const startMonth = Form.useWatch('start_month', form);
  const endMonth = Form.useWatch('end_month', form);

  const onFinish = async (values: FormValues) => {
    setSaving(true);
    try {
      const payload = toWrite(values);
      if (period) await updateSeasonalPeriod(period.id, payload);
      else await createSeasonalPeriod(payload);
      message.success(period ? 'Entry saved' : 'Entry added');
      onSaved();
    } catch (err) {
      const api = apiFieldError(err);
      if (api?.code === 'seasonal_period_name_taken') {
        form.setFields([{ name: 'name', errors: [api.message] }]);
      } else {
        message.error(`${period ? 'Save' : 'Create'} failed: ${errorMessage(err)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const required = [{ required: true, message: 'Required' }];
  return (
    <Modal
      open
      title={period ? `Edit “${period.name}”` : 'New calendar entry'}
      onCancel={onClose}
      destroyOnHidden
      okText={period ? 'Save' : 'Add'}
      okButtonProps={{ loading: saving }}
      onOk={() => form.submit()}
    >
      <Form<FormValues>
        form={form}
        layout="vertical"
        onFinish={(v) => void onFinish(v)}
        initialValues={{
          name: period?.name ?? '',
          kind: period?.kind ?? 'recurring',
          start_month: period?.start_month ?? undefined,
          start_day: period?.start_day ?? undefined,
          end_month: period?.end_month ?? undefined,
          end_day: period?.end_day ?? undefined,
          start_date: period?.start_date ?? undefined,
          end_date: period?.end_date ?? undefined,
          series: period?.series ?? undefined,
          note: period?.note ?? undefined,
        }}
      >
        <Form.Item name="name" label="Name" rules={[...required, { whitespace: true }]}>
          <Input maxLength={128} placeholder="e.g. Black Friday" />
        </Form.Item>
        <Form.Item name="kind" label="Repeats">
          <Radio.Group
            options={[
              { value: 'recurring', label: 'Every year on the same dates' },
              { value: 'dated', label: 'On specific dates (a period that moves)' },
            ]}
          />
        </Form.Item>
        {kind === 'recurring' ? (
          <>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -8 }}>
              The range may cross the year end (20 December to 5 January). 29 February is taken as
              28 February in other years.
            </Typography.Paragraph>
            <Flex gap={12} wrap>
              <Form.Item name="start_month" label="From month" rules={required} style={{ flex: 1 }}>
                <Select options={MONTH_OPTIONS} placeholder="Month" />
              </Form.Item>
              <Form.Item name="start_day" label="From day" rules={required} style={{ flex: 1 }}>
                <InputNumber min={1} max={daysIn(startMonth)} style={{ width: '100%' }} />
              </Form.Item>
            </Flex>
            <Flex gap={12} wrap>
              <Form.Item name="end_month" label="To month" rules={required} style={{ flex: 1 }}>
                <Select options={MONTH_OPTIONS} placeholder="Month" />
              </Form.Item>
              <Form.Item name="end_day" label="To day" rules={required} style={{ flex: 1 }}>
                <InputNumber min={1} max={daysIn(endMonth)} style={{ width: '100%' }} />
              </Form.Item>
            </Flex>
          </>
        ) : (
          <>
            <Flex gap={12} wrap>
              <Form.Item name="start_date" label="First day" rules={required} style={{ flex: 1 }}>
                <Input type="date" />
              </Form.Item>
              <Form.Item
                name="end_date"
                label="Last day"
                dependencies={['start_date']}
                style={{ flex: 1 }}
                rules={[
                  ...required,
                  ({ getFieldValue }) => ({
                    validator: (_: unknown, value: string | undefined) => {
                      const start = getFieldValue('start_date') as string | undefined;
                      return start && value && value < start
                        ? Promise.reject(new Error('The last day is before the first'))
                        : Promise.resolve();
                    },
                  }),
                ]}
              >
                <Input type="date" />
              </Form.Item>
            </Flex>
            <Form.Item
              name="series"
              label="Series (optional)"
              extra="Dated entries that share a series are one period across years, which is how this year’s sale finds last year’s. Give each year’s entry the same series and attach them all to the check."
            >
              <Input maxLength={128} placeholder="e.g. Summer sale" />
            </Form.Item>
          </>
        )}
        <Form.Item name="note" label="Note (optional)">
          <Input.TextArea maxLength={2000} rows={2} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
