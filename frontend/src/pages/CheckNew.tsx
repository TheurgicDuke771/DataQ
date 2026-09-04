import { Alert, App, Button, Card, Flex, Form, Input, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { getConnection, listConnections } from '../api/connections';
import { createCheck, getSuite } from '../api/suites';
import { buildCheckPayload, buildComparisonPayload } from '../components/checks/checkForm';
import { ComparisonCheckForm } from '../components/checks/ComparisonCheckForm';
import {
  ConfigFieldItem,
  DimensionField,
  EngineField,
  SeverityThresholdFields,
} from '../components/checks/checkFormFields';
import { ColumnProfilePanel } from '../components/checks/ColumnProfilePanel';
import { DryRunPreview } from '../components/checks/DryRunPreview';
import { SqlGeneratePanel } from '../components/checks/SqlGeneratePanel';
import { CUSTOM_SQL_EXPECTATION_TYPE, isCustomSql } from '../components/checks/customSql';
import {
  configFieldsFor,
  effectiveEngineFor,
  EXPECTATION_BY_TYPE,
  expectationsByCategoryFor,
  fieldVisible,
  showEngineChoiceFor,
  type ExpectationCategory,
} from '../components/checks/expectationCatalog';
import { Page } from '../components/layout/Page';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { apiFieldError } from '../utils/fieldErrors';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page check authoring flow (GX-Cloud style): pick a category → pick an expectation
 * → fill its config + thresholds.
 */
export function CheckNew() {
  const navigate = useNavigate();
  const { suiteId } = useParams<{ suiteId: string }>();
  const { message } = App.useApp();
  const [category, setCategory] = useState<ExpectationCategory>();
  const [expectationType, setExpectationType] = useState<string>();
  const [form] = Form.useForm();
  const column = Form.useWatch(['config', 'column'], form) as string | undefined;
  // Drives which conditional fields render (anomaly's `column`, #593 — ConfigField.showWhen) — the
  // whole `config` object, not just one key.
  const configValues = Form.useWatch('config', form) as Record<string, unknown> | undefined;
  const engineChoice = Form.useWatch('engine', form) as string | undefined;
  const { run, loading: submitting } = useAsyncAction('Create failed');
  // Load the suite + its connection together: the run target (#215) drives the dry-run preview's
  // table/schema, and the connection type gates the Custom-SQL category (ADR 0019 — SQL
  // datasources only).
  const { state } = useAsyncData(async () => {
    if (!suiteId) throw new Error('no suite');
    const suite = await getSuite(suiteId);
    // Best-effort: a suite may be readable while its connection isn't (shared suite).
    const connection = await getConnection(suite.connection_id).catch(() => null);
    // The comparison editor's source picker; best-effort like the connection.
    const connections = await listConnections().catch(() => []);
    return { suite, connection, connections };
  });
  const target = state.status === 'ok' ? state.data.suite.target : null;
  const connectionType = state.status === 'ok' ? state.data.connection?.type : undefined;
  const categories = expectationsByCategoryFor(connectionType);

  const backToSuite = () => navigate(suiteId ? `/suites/${suiteId}` : '/suites');
  const spec = expectationType ? EXPECTATION_BY_TYPE[expectationType] : undefined;
  const showEngineChoice = showEngineChoiceFor(spec, connectionType);
  const effectiveEngine = effectiveEngineFor(spec, connectionType, engineChoice);

  // Start the config form clean each time an expectation is (re)picked — after the <Form> mounts
  // (it only renders in the config step).
  useEffect(() => {
    if (expectationType) form.resetFields();
  }, [expectationType, form]);

  // A save refusal that has no form field to land on (see below). Kept in state
  // rather than shown as a toast so it stays on screen while the author reacts.
  const [refusal, setRefusal] = useState<string | null>(null);

  const onFinish = (values: Record<string, unknown>) => {
    setRefusal(null);
    if (!suiteId || !expectationType) return;
    const isComparison = EXPECTATION_BY_TYPE[expectationType]?.kind === 'comparison';
    return run(async () => {
      try {
        await createCheck(
          suiteId,
          isComparison
            ? buildComparisonPayload({ ...values, expectation_type: expectationType })
            : buildCheckPayload({ ...values, expectation_type: expectationType }),
        );
      } catch (err) {
        // A refusal about the *chosen expectation* (the sampling to row-count conflict, #1333 F5)
        // has no form field to land on here — the type was picked in the previous step.
        const api = apiFieldError(err);
        if (api && typeof api.detail.field === 'string') {
          setRefusal(api.message);
          return;
        }
        throw err;
      }
      message.success(`${values.name as string}: created`);
      backToSuite();
    });
  };

  // Step 3 — config + thresholds for the chosen expectation.
  if (spec) {
    return (
      <Page width={'form'}>
        <Header
          title={spec.label}
          onBack={() => setExpectationType(undefined)}
          backLabel="Back to expectations"
        />
        <Form form={form} layout="vertical" onFinish={onFinish}>
          <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
            {spec.description}
          </Typography.Paragraph>
          {refusal && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              data-testid="check-refusal"
              title="This check can’t be added to the suite"
              description={refusal}
            />
          )}
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="e.g. order_id not null" />
          </Form.Item>
          {showEngineChoice && (
            <EngineField
              dmfCapability={
                state.status === 'ok' ? state.data.connection?.engine_capabilities?.dmf : undefined
              }
            />
          )}
          {suiteId && isCustomSql(expectationType) && (
            <Form.Item>
              <SqlGeneratePanel suiteId={suiteId} form={form} />
            </Form.Item>
          )}
          {spec.kind === 'comparison' ? (
            <ComparisonCheckForm
              connections={state.status === 'ok' ? state.data.connections : []}
              suiteConnectionName={state.status === 'ok' ? state.data.connection?.name : undefined}
              suiteConnectionType={connectionType}
              targetSummary={targetSummary(target)}
            />
          ) : (
            configFieldsFor(spec, connectionType)
              .filter((field) => fieldVisible(field, configValues))
              .map((field) => (
                <ConfigFieldItem
                  key={field.name}
                  field={field}
                  connectionType={connectionType}
                  configValues={configValues}
                />
              ))
          )}
          <DimensionField spec={spec} initialValue={spec.dimension} />
          {!spec.noThresholds && <SeverityThresholdFields monitor={spec.thresholds} />}
          {suiteId && spec.kind !== 'comparison' && (
            <>
              <Form.Item>
                <ColumnProfilePanel suiteId={suiteId} target={target} column={column} />
              </Form.Item>
              {!spec.kind && effectiveEngine === 'gx' && (
                <Form.Item>
                  <DryRunPreview
                    suiteId={suiteId}
                    expectationType={expectationType}
                    target={target}
                    form={form}
                  />
                </Form.Item>
              )}
            </>
          )}
          <Flex justify="end" gap={8}>
            <Button onClick={() => setExpectationType(undefined)}>Back</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>
              Create check
            </Button>
          </Flex>
        </Form>
      </Page>
    );
  }

  // Step 2 — pick an expectation within the chosen category. Custom SQL's real catalog entry
  // (`group.specs`) has exactly one item — the check it describes IS the one GX expectation,
  // and that count must stay accurate. "Generate from a description" beside it is a picker-only
  // shortcut, not a second expectation type: it sets the SAME expectationType (below), so the
  // resulting config form is identical either way — the choice here is only about how you'd
  // rather start, hand-writing or describing the rule for the model to translate.
  if (category) {
    const group = categories.find((g) => g.category === category);
    return (
      <Page width={'form'}>
        <Header
          title={category}
          onBack={() => setCategory(undefined)}
          backLabel="Back to categories"
        />
        <Flex wrap gap={12}>
          {group?.specs.map((e) => (
            <Card
              key={e.type}
              hoverable
              size="small"
              style={{ width: 320 }}
              onClick={() => setExpectationType(e.type)}
            >
              <Typography.Text strong>{e.label}</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
                {e.description}
              </Typography.Paragraph>
            </Card>
          ))}
          {category === 'Custom SQL' && (
            <Card
              hoverable
              size="small"
              style={{ width: 320 }}
              onClick={() => setExpectationType(CUSTOM_SQL_EXPECTATION_TYPE)}
            >
              <Typography.Text strong>Generate from a description</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
                Describe the rule in plain language — DataQ writes the SQL.
              </Typography.Paragraph>
            </Card>
          )}
        </Flex>
      </Page>
    );
  }

  // Step 1 — pick a category.
  return (
    <Page width={'form'}>
      <Header title="New check" onBack={backToSuite} backLabel="Cancel" />
      <Flex wrap gap={12}>
        {categories.map((g) => (
          <Card
            key={g.category}
            hoverable
            size="small"
            style={{ width: 220 }}
            onClick={() => setCategory(g.category)}
          >
            <Typography.Text strong>{g.category}</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
              {g.specs.length} expectation{g.specs.length === 1 ? '' : 's'}
            </Typography.Paragraph>
          </Card>
        ))}
      </Flex>
    </Page>
  );
}

/** Human summary of the suite's run target for the locked target pane. */
function targetSummary(target: Record<string, unknown> | null | undefined): string {
  if (!target) return '(no run target set)';
  const t = target as {
    catalog?: string;
    schema?: string;
    table?: string;
    path?: string;
    pattern?: string;
  };
  if (t.path) return t.path;
  if (t.pattern) return `batch: ${t.pattern}`;
  return [t.catalog, t.schema, t.table].filter(Boolean).join('.') || '(no run target set)';
}

function Header({
  title,
  onBack,
  backLabel,
}: {
  title: string;
  onBack: () => void;
  backLabel: string;
}) {
  return (
    <Flex justify="space-between" align="center" gap={12} wrap>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {title}
      </Typography.Title>
      <Button onClick={onBack}>{backLabel}</Button>
    </Flex>
  );
}
