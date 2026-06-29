import { App, Button, Card, Flex, Form, Input, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { getConnection } from '../api/connections';
import { createCheck, getSuite } from '../api/suites';
import { buildCheckPayload } from '../components/checks/checkForm';
import { ConfigFieldItem, SeverityThresholdFields } from '../components/checks/checkFormFields';
import { ColumnProfilePanel } from '../components/checks/ColumnProfilePanel';
import { DryRunPreview } from '../components/checks/DryRunPreview';
import {
  EXPECTATION_BY_TYPE,
  expectationsByCategoryFor,
  type ExpectationCategory,
} from '../components/checks/expectationCatalog';
import { Page } from '../components/layout/Page';
import { useAsyncData } from '../hooks/useAsyncData';

// Monitor-kind categories still reserved by ADR 0012 — surfaced (disabled) so the
// roadmap is visible. Freshness + Volume are now authorable (real catalog
// categories, SQL-datasource-gated); Schema drift remains a v1.x auto-monitor.
const RESERVED_CATEGORIES = ['Schema drift'] as const;

/**
 * Dedicated full-page check authoring flow (GX-Cloud style): pick a category →
 * pick an expectation → fill its config + thresholds. Editing an existing check
 * still uses the lighter drawer on the suite detail panel.
 */
export function CheckNew() {
  const navigate = useNavigate();
  const { suiteId } = useParams<{ suiteId: string }>();
  const { message } = App.useApp();
  const [category, setCategory] = useState<ExpectationCategory>();
  const [expectationType, setExpectationType] = useState<string>();
  const [form] = Form.useForm();
  const column = Form.useWatch(['config', 'column'], form) as string | undefined;
  const [submitting, setSubmitting] = useState(false);
  // Load the suite + its connection together: the run target (#215) drives the
  // dry-run preview's table/schema, and the connection type gates the Custom-SQL
  // category (ADR 0019 — SQL datasources only).
  const { state } = useAsyncData(async () => {
    if (!suiteId) throw new Error('no suite');
    const suite = await getSuite(suiteId);
    // Best-effort: a suite may be readable while its connection isn't (shared
    // suite). The connection only gates the Custom-SQL category — never let its
    // absence break the rest of the page (target / dry-run / profiler).
    const connection = await getConnection(suite.connection_id).catch(() => null);
    return { suite, connection };
  });
  const target = state.status === 'ok' ? state.data.suite.target : null;
  const connectionType = state.status === 'ok' ? state.data.connection?.type : undefined;
  const categories = expectationsByCategoryFor(connectionType);

  const backToSuite = () => navigate(suiteId ? `/suites/${suiteId}` : '/suites');
  const spec = expectationType ? EXPECTATION_BY_TYPE[expectationType] : undefined;

  // Start the config form clean each time an expectation is (re)picked — after
  // the <Form> mounts (it only renders in the config step), so the store is
  // connected and a re-pick can't leak the prior expectation's fields.
  useEffect(() => {
    if (expectationType) form.resetFields();
  }, [expectationType, form]);

  const onFinish = async (values: Record<string, unknown>) => {
    if (!suiteId || !expectationType) return;
    setSubmitting(true);
    try {
      await createCheck(
        suiteId,
        buildCheckPayload({ ...values, expectation_type: expectationType }),
      );
      message.success(`${values.name as string}: created`);
      backToSuite();
    } catch (err) {
      message.error(`Create failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
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
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="e.g. order_id not null" />
          </Form.Item>
          {spec.fields.map((field) => (
            <ConfigFieldItem key={field.name} field={field} />
          ))}
          <SeverityThresholdFields monitor={spec.thresholds} />
          {suiteId && (
            <>
              <Form.Item>
                <ColumnProfilePanel suiteId={suiteId} target={target} column={column} />
              </Form.Item>
              {/* Dry-run previews a GX expectation; monitor kinds run a scalar SQL
                  aggregate, not GX, so the GX dry-run path doesn't apply. */}
              {!spec.kind && (
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

  // Step 2 — pick an expectation within the chosen category.
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
        {RESERVED_CATEGORIES.map((label) => (
          <Card key={label} size="small" style={{ width: 220, opacity: 0.55 }}>
            <Flex justify="space-between" align="center" gap={8}>
              <Typography.Text strong>{label}</Typography.Text>
              <Tag>v1.x</Tag>
            </Flex>
            <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
              Auto-monitor — coming soon
            </Typography.Paragraph>
          </Card>
        ))}
      </Flex>
    </Page>
  );
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
    <Flex justify="space-between" align="center" gap={12}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {title}
      </Typography.Title>
      <Button onClick={onBack}>{backLabel}</Button>
    </Flex>
  );
}
