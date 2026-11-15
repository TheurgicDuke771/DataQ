import { Alert, AutoComplete, Flex, Select, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { describeSpan, listSeasonalPeriods, type SeasonalPeriod } from '../../api/seasonalPeriods';
import { listColumns } from '../../api/suites';
import { useIsWorkspaceAdmin } from '../../auth/useMe';
import { useAsyncData } from '../../hooks/useAsyncData';
import { extractProfileTarget } from './columnTarget';

/** Where a column picker reads the column names from: the suite's run target. */
export interface ColumnSource {
  suiteId: string;
  target: Record<string, unknown> | null;
}

/** Free text with the target's columns as suggestions, read once on first focus. */
export function ColumnPickerInput({
  value,
  onChange,
  source,
  id,
}: {
  value?: string;
  onChange?: (value: string) => void;
  source?: ColumnSource;
  id?: string;
}) {
  const target = source ? extractProfileTarget(source.target) : null;
  const [cols, setCols] = useState<'idle' | 'loading' | 'error' | string[]>('idle');
  const load = () => {
    if (cols !== 'idle' || !source || !target) return;
    setCols('loading');
    listColumns(source.suiteId, target)
      .then(setCols)
      .catch(() => setCols('error'));
  };
  return (
    <Flex vertical gap={2}>
      <AutoComplete
        id={id}
        value={value}
        onChange={onChange}
        onFocus={load}
        options={Array.isArray(cols) ? cols.map((c) => ({ value: c })) : []}
        showSearch={{
          filterOption: (input, option) =>
            String(option?.value ?? '')
              .toLowerCase()
              .includes(input.toLowerCase()),
        }}
        placeholder={target ? 'Pick or type a column' : 'Column name'}
      />
      {cols === 'error' && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          The table’s columns could not be read; type the name.
        </Typography.Text>
      )}
    </Flex>
  );
}

function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.('timeZone') ?? [];
    return zones.includes('UTC') ? zones : ['UTC', ...zones];
  } catch {
    return ['UTC'];
  }
}

/** An IANA time zone, picked from the browser's list or typed. */
export function TimezoneInput({
  value,
  onChange,
  id,
}: {
  value?: string;
  onChange?: (value: string) => void;
  id?: string;
}) {
  const options = useMemo(() => timeZones().map((z) => ({ value: z })), []);
  return (
    <AutoComplete
      id={id}
      value={value}
      onChange={onChange}
      options={options}
      showSearch={{
        filterOption: (input, option) =>
          String(option?.value ?? '')
            .toLowerCase()
            .includes(input.toLowerCase()),
      }}
      placeholder="UTC"
    />
  );
}

function entryLabel(p: SeasonalPeriod): string {
  return `${p.name} (${describeSpan(p)})`;
}

/**
 * The calendar entries attached to a per-period check. Attaching needs yearly seasonality on;
 * with it off the picker is replaced by what a save will do to entries already attached.
 */
export function CalendarEntriesInput({
  value,
  onChange,
  yearly,
  id,
}: {
  value?: string[];
  onChange?: (value: string[]) => void;
  yearly: boolean;
  id?: string;
}) {
  const { state } = useAsyncData(listSeasonalPeriods);
  const isAdmin = useIsWorkspaceAdmin();
  const attached = value ?? [];
  const periods = state.status === 'ok' ? state.data : [];
  const byId = new Map(periods.map((p) => [p.id, p]));

  if (!yearly) {
    return attached.length > 0 ? (
      <Alert
        type="warning"
        showIcon
        data-testid="calendar-detach-warning"
        title={`Saving removes ${attached.length} attached calendar entr${attached.length === 1 ? 'y' : 'ies'}`}
        description="Calendar entries apply only with yearly seasonality on. Turn it back on to keep them."
      />
    ) : (
      <Typography.Text type="secondary" data-testid="calendar-needs-yearly">
        Turn on yearly seasonality to attach calendar entries.
      </Typography.Text>
    );
  }

  return (
    <Flex vertical gap={4}>
      <Select
        id={id}
        mode="multiple"
        allowClear
        value={attached}
        onChange={onChange}
        loading={state.status === 'loading'}
        placeholder={periods.length === 0 ? 'No entries on the calendar yet' : 'Pick entries'}
        showSearch={{ optionFilterProp: 'label' }}
        options={[
          ...periods.map((p) => ({ value: p.id, label: entryLabel(p) })),
          // An attached id the calendar no longer lists is kept visible rather than dropped.
          ...attached
            .filter((i) => !byId.has(i))
            .map((i) => ({
              value: i,
              label:
                state.status === 'ok'
                  ? `Not on the calendar (${i})`
                  : `Entry ${i} (calendar not loaded)`,
            })),
        ]}
      />
      {state.status === 'error' && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          The calendar could not be loaded.
        </Typography.Text>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {isAdmin ? (
          <>
            Entries are managed in <Link to="/admin/calendar">Admin → Calendar</Link>.
          </>
        ) : (
          'Entries are managed by workspace admins.'
        )}
      </Typography.Text>
    </Flex>
  );
}
