import { Alert, App, Card, Collapse, Flex, Switch, Table, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';

import {
  getTelemetrySettings,
  putTelemetrySettings,
  resetTelemetryStatus,
  type TelemetryEventSpec,
  type TelemetrySettings,
} from '../../api/telemetry';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { fetchFailure } from '../../utils/errors';

const SINK_LABEL: Record<TelemetrySettings['sink'], string> = {
  off: 'No sink configured',
  file: 'JSON-lines file',
  otlp: 'OpenTelemetry exporter',
};

function describeValues(values: string[] | 'integer'): string {
  return values === 'integer' ? 'a count' : values.join(', ');
}

/** The usage-telemetry opt-in, with the full event list it would send shown inline. */
export function TelemetryPanel() {
  const { state } = useAsyncData(() => getTelemetrySettings());
  const [local, setLocal] = useState<TelemetrySettings | null>(null);
  const { run, loading } = useAsyncAction('Could not change usage telemetry');
  const current = local ?? (state.status === 'ok' ? state.data : null);

  const { message } = App.useApp();
  // The switch is disabled while it saves, which drops focus; put it back once it is enabled.
  const switchRef = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!loading && refocus.current) {
      refocus.current = false;
      switchRef.current?.focus();
    }
  }, [loading]);
  const onToggle = (next: boolean) => {
    refocus.current = document.activeElement === switchRef.current;

    void run(async () => {
      setLocal(await putTelemetrySettings(next));
      resetTelemetryStatus();
      message.success(next ? 'Usage telemetry turned on' : 'Usage telemetry turned off');
    });
  };

  return (
    <Card title="Usage telemetry" size="small">
      {state.status === 'loading' && !current && (
        <Typography.Text type="secondary">Loading…</Typography.Text>
      )}
      {state.status === 'error' && !current && (
        <Alert
          type="error"
          showIcon
          title="Could not load the usage-telemetry settings"
          description={fetchFailure(state.error).message}
        />
      )}
      {current && (
        <Flex vertical gap={12}>
          <Flex align="center" gap={12} wrap>
            <Switch
              ref={switchRef}
              checked={current.stored}
              disabled={loading}
              loading={loading}
              onChange={onToggle}
              aria-label="Usage telemetry"
            />
            <Typography.Text strong>Record how this workspace is used</Typography.Text>
            <Tag color={current.enabled ? 'green' : 'default'}>
              {current.enabled ? 'Recording' : 'Off'}
            </Tag>
            <Tag>{SINK_LABEL[current.sink]}</Tag>
          </Flex>
          <Typography.Text type="secondary">
            Off by default. When on, DataQ records the events below as OpenTelemetry metrics and log
            events, to a file or collector your operator chose. Only event names, counts and the
            listed values are recorded: never a table or column name, SQL, a suite or check name, an
            email address or a credential. Nothing is sent to DataQ&apos;s authors. Turning it off
            takes up to 30 seconds to reach every DataQ process.
          </Typography.Text>
          {current.stored && current.sink === 'off' && (
            <Alert
              type="warning"
              showIcon
              title="Opted in, but nothing is recorded"
              description="This deployment has no telemetry sink (TELEMETRY_SINK is off). The operator has to set it to file or otlp before anything is recorded."
            />
          )}
          {current.stored && current.sink !== 'off' && !current.sink_ready && (
            <Alert
              type="warning"
              showIcon
              title="Opted in, but the sink could not be started"
              description="TELEMETRY_SINK is set, but DataQ could not start it (for example, the file path is not writable). Nothing is recorded; the server log says why."
            />
          )}
          <Collapse
            size="small"
            items={[
              {
                key: 'events',
                label: `What is recorded (${current.events.length} events)`,
                children: (
                  <Table<TelemetryEventSpec>
                    size="small"
                    rowKey="name"
                    pagination={false}
                    dataSource={current.events}
                    columns={[
                      {
                        title: 'Event',
                        dataIndex: 'name',
                        render: (name: string) => <Typography.Text code>{name}</Typography.Text>,
                      },
                      {
                        title: 'Recorded with',
                        dataIndex: 'attributes',
                        render: (attributes: TelemetryEventSpec['attributes']) => (
                          <Flex vertical gap={2}>
                            {Object.entries(attributes).map(([key, values]) => (
                              <Typography.Text key={key} style={{ fontSize: 12 }}>
                                <Typography.Text code>{key}</Typography.Text>:{' '}
                                {describeValues(values)}
                              </Typography.Text>
                            ))}
                          </Flex>
                        ),
                      },
                    ]}
                  />
                ),
              },
            ]}
          />
          {current.updated_at && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Last changed {formatTimestamp(current.updated_at)}
              {current.updated_by ? ` by ${current.updated_by}` : ''}.
            </Typography.Text>
          )}
        </Flex>
      )}
    </Card>
  );
}
