import { Card, Col, Form, Input, InputNumber, Radio, Row, Select, Tag, Typography } from 'antd';
import { Suspense, lazy, useEffect, useMemo, useRef } from 'react';

import {
  DATASOURCE_TYPES,
  isSqlQueryable,
  type Connection,
  type ConnectionType,
} from '../../api/connections';

const SqlEditorField = lazy(() => import('./SqlEditorField'));

/**
 * The ADR 0015 §5 side-by-side comparison editor: **left = source** (the
 * baseline — connection picker, table or read-only SQL, per the picked
 * connection's type), **right = target** (locked to the suite's connection —
 * the model's §1 invariant made visible — with an optional SQL projection).
 * Common options (join keys, row cap) sit below; the severity thresholds are
 * rendered by the parent (shared `SeverityThresholdFields`).
 *
 * Field names bind into the parent `<Form>`; `buildComparisonPayload`
 * (checkForm.ts) assembles them into the ADR 0015 config shape.
 */
export function ComparisonCheckForm({
  connections,
  suiteConnectionName,
  suiteConnectionType,
  targetSummary,
}: {
  /** All connections — filtered to datasources for the source picker. */
  connections: Connection[];
  suiteConnectionName: string | undefined;
  suiteConnectionType: ConnectionType | undefined;
  /** Human summary of the suite's run target (table/path), shown locked. */
  targetSummary: string;
}) {
  const form = Form.useFormInstance();
  const sourceMode = (Form.useWatch('source_mode', form) as string | undefined) ?? 'table';
  const sourceId = Form.useWatch('source_connection_id', form) as string | undefined;
  const datasources = useMemo(
    () => connections.filter((c) => (DATASOURCE_TYPES as string[]).includes(c.type)),
    [connections],
  );
  const sourceType = datasources.find((c) => c.id === sourceId)?.type as ConnectionType | undefined;
  const sourceSql = sourceType !== undefined && isSqlQueryable(sourceType);
  // Repointing the source connection resets the dataset fields: antd preserves
  // unmounted field values, so a stale hidden SQL-mode query (picked on a SQL
  // source) would otherwise silently win over the visible table/path fields
  // after switching to a non-SQL source — a 422 the form gives no hint about.
  const prevSourceId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (prevSourceId.current !== undefined && prevSourceId.current !== sourceId) {
      form.setFieldsValue({ source_mode: 'table', source_query: undefined, source: {} });
    }
    prevSourceId.current = sourceId;
  }, [sourceId, form]);
  const targetSql = suiteConnectionType !== undefined && isSqlQueryable(suiteConnectionType);

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Card size="small" title="Source (baseline)" data-testid="comparison-source-pane">
            <Form.Item
              name="source_connection_id"
              label="Source connection"
              rules={[{ required: true, message: 'Pick the baseline connection' }]}
            >
              <Select
                placeholder="Baseline connection"
                options={datasources.map((c) => ({
                  value: c.id,
                  label: `${c.name} (${c.type}, ${c.env})`,
                }))}
                showSearch
                optionFilterProp="label"
              />
            </Form.Item>
            {sourceSql && (
              <Form.Item name="source_mode" label="Dataset" initialValue="table">
                <Radio.Group
                  options={[
                    { label: 'Table', value: 'table' },
                    { label: 'SQL query', value: 'query' },
                  ]}
                  optionType="button"
                  size="small"
                />
              </Form.Item>
            )}
            {sourceMode === 'query' && sourceSql ? (
              <Form.Item
                name="source_query"
                label="Source query (read-only)"
                rules={[{ required: true, message: 'A SELECT/WITH query' }]}
              >
                <Suspense fallback={<Input.TextArea rows={4} disabled />}>
                  <SqlEditorField />
                </Suspense>
              </Form.Item>
            ) : (
              <SourceDatasetFields sourceType={sourceType} />
            )}
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title="Target (this suite)" data-testid="comparison-target-pane">
            <Form.Item label="Target connection">
              {/* Locked by design: the suite IS the target under test (ADR 0015 §1). */}
              <Input
                value={suiteConnectionName ?? '(suite connection)'}
                disabled
                data-testid="comparison-target-connection"
                suffix={<Tag style={{ marginRight: 0 }}>locked</Tag>}
              />
            </Form.Item>
            <Form.Item label="Target dataset">
              <Input value={targetSummary} disabled />
            </Form.Item>
            {targetSql && (
              <Form.Item
                name="target_query"
                label="Optional SQL projection (runs on the suite connection)"
              >
                <Suspense fallback={<Input.TextArea rows={4} disabled />}>
                  <SqlEditorField />
                </Suspense>
              </Form.Item>
            )}
          </Card>
        </Col>
      </Row>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 8 }}>
        Rows are joined on the key columns; matched rows compare their remaining shared columns.
      </Typography.Paragraph>
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Form.Item
            name="keys"
            label="Join key columns"
            rules={[{ required: true, message: 'At least one key column' }]}
          >
            <Select
              mode="tags"
              placeholder="e.g. order_id"
              tokenSeparators={[',']}
              open={false}
              suffixIcon={null}
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name="max_rows"
            label="Row cap (per side)"
            help="Blank = the server default. Over-cap runs fail fast — never a truncated diff."
          >
            <InputNumber min={1} style={{ width: '100%' }} placeholder="100000" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Form.Item
            name="tolerance_absolute"
            label="Numeric tolerance — absolute"
            help="Numbers within ±this count as equal (e.g. 0.01 for cents)."
          >
            <InputNumber min={0} style={{ width: '100%' }} placeholder="0" step={0.01} />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name="tolerance_relative"
            label="Numeric tolerance — relative"
            help="Fraction of the larger value (1e-6 absorbs float32/float64 round-trips)."
          >
            <InputNumber min={0} style={{ width: '100%' }} placeholder="0" step={0.000001} />
          </Form.Item>
        </Col>
      </Row>
    </>
  );
}

/** Dataset address fields tailored to the picked source connection's type. */
function SourceDatasetFields({ sourceType }: { sourceType: ConnectionType | undefined }) {
  if (sourceType === 'adls_gen2' || sourceType === 's3') {
    return (
      <Form.Item
        name={['source', 'path']}
        label="File path"
        rules={[{ required: true, message: 'Object path, e.g. exports/orders.csv' }]}
      >
        <Input placeholder="exports/orders.csv" />
      </Form.Item>
    );
  }
  return (
    <>
      {sourceType === 'unity_catalog' && (
        <Form.Item
          name={['source', 'catalog']}
          label="Catalog"
          rules={[{ required: true, message: 'Unity Catalog needs a catalog' }]}
        >
          <Input placeholder="main" />
        </Form.Item>
      )}
      {sourceType === 'iceberg' ? (
        <Form.Item name={['source', 'namespace']} label="Namespace">
          <Input placeholder="retail" />
        </Form.Item>
      ) : (
        <Form.Item name={['source', 'schema']} label="Schema">
          <Input placeholder="(connection default)" />
        </Form.Item>
      )}
      <Form.Item
        name={['source', 'table']}
        label="Table"
        rules={[{ required: true, message: 'The baseline table' }]}
      >
        <Input placeholder="ORDERS" />
      </Form.Item>
    </>
  );
}
