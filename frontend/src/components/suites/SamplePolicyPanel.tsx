import { App, Alert, Button, Card, Flex, Select, Spin, Typography } from 'antd';
import { useMemo, useState } from 'react';

import {
  type ColumnPolicy,
  getColumnPolicy,
  setColumnPolicy,
  suggestColumnPolicy,
} from '../../api/columnPolicy';
import { type ColumnTarget, listColumns, type Suite, targetString } from '../../api/suites';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';

/**
 * Suite-detail panel for the failing-sample redaction policy (#415): which column
 * locates a failing row (`identifier_column`, always shown) and which columns are
 * PII (`pii_columns`, always masked). The classifier auto-classifies incidental
 * columns at redaction time regardless; this pins the shown identifier + the masked
 * set. "Auto-detect" profiles + classifies the target for a suggestion. `view` reads,
 * `edit` (`canManage`) mutates — matching the backend gate.
 */
export function SamplePolicyPanel({ suite, canManage }: { suite: Suite; canManage: boolean }) {
  const { state, reload } = useAsyncData(() => getColumnPolicy(suite.id));

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Failing-sample columns</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Which column locates a failing row, and which are masked as PII.
          </Typography.Text>
        </Flex>
      }
    >
      {state.status === 'loading' ? (
        <Spin description="Loading policy…" />
      ) : state.status === 'error' ? (
        <Alert type="error" showIcon title="Failed to load policy" description={state.error} />
      ) : (
        <SamplePolicyForm
          key={`${state.data.identifier_column}:${state.data.pii_columns.join(',')}`}
          suite={suite}
          canManage={canManage}
          initial={state.data}
          onSaved={reload}
        />
      )}
    </Card>
  );
}

function SamplePolicyForm({
  suite,
  canManage,
  initial,
  onSaved,
}: {
  suite: Suite;
  canManage: boolean;
  initial: ColumnPolicy;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [identifier, setIdentifier] = useState<string | null>(initial.identifier_column);
  const [pii, setPii] = useState<string[]>(initial.pii_columns);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);

  // The suite's target as a column-introspection target (null for a batch/pattern
  // target with no fixed table/file — those can't be introspected).
  const columnTarget = useMemo<ColumnTarget | null>(() => {
    if (suite.target?.pattern) return null;
    const table = targetString(suite.target, 'table');
    const path = targetString(suite.target, 'path');
    if (!table && !path) return null;
    return {
      table,
      schema: targetString(suite.target, 'schema'),
      catalog: targetString(suite.target, 'catalog'),
      // Iceberg addresses `namespace.table`; the namespace rides alongside table.
      namespace: targetString(suite.target, 'namespace'),
      path,
      file_format: targetString(suite.target, 'file_format') as 'csv' | 'parquet' | undefined,
    };
  }, [suite.target]);

  // Introspected columns (#635) — fetched lazily the first time a dropdown opens (a
  // live warehouse round-trip, so not on mount). Degrades to free-tag entry when
  // introspection is empty/unavailable: the Selects stay mode="tags".
  const [cols, setCols] = useState<
    { status: 'idle' | 'loading' | 'error' } | { status: 'loaded'; columns: string[] }
  >({ status: 'idle' });

  const loadColumns = () => {
    if (cols.status !== 'idle' || !columnTarget) return; // fetch once
    setCols({ status: 'loading' });
    listColumns(suite.id, columnTarget)
      .then((columns) => setCols({ status: 'loaded', columns }))
      .catch(() => setCols({ status: 'error' }));
  };

  const introspected = cols.status === 'loaded' ? cols.columns : [];
  // Union of the target's real columns + anything the user has already named, so a
  // saved value renders even if introspection didn't return it (a view-hidden or
  // newly-added column), and free-typing still works.
  const known = Array.from(new Set([...(identifier ? [identifier] : []), ...pii]));
  const options = Array.from(new Set([...introspected, ...known])).map((c) => ({
    value: c,
    label: c,
  }));

  const onSuggest = async () => {
    setSuggesting(true);
    try {
      const suggestion = await suggestColumnPolicy(suite.id, {
        table: targetString(suite.target, 'table'),
        schema: targetString(suite.target, 'schema'),
        catalog: targetString(suite.target, 'catalog'),
        // Iceberg addresses `namespace.table`; the namespace rides alongside table.
        namespace: targetString(suite.target, 'namespace'),
        path: targetString(suite.target, 'path'),
        file_format: targetString(suite.target, 'file_format') as 'csv' | 'parquet' | undefined,
      });
      setIdentifier(suggestion.identifier_column);
      setPii(suggestion.pii_columns);
      message.success('Suggested from the target — review, then Save');
    } catch (err) {
      message.error(`Auto-detect failed: ${errorMessage(err)}`);
    } finally {
      setSuggesting(false);
    }
  };

  const onSave = async () => {
    setSaving(true);
    try {
      await setColumnPolicy(suite.id, { identifier_column: identifier, pii_columns: pii });
      message.success('Sample policy saved');
      onSaved();
    } catch (err) {
      message.error(`Save failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const identifierIsPii = !!identifier && pii.includes(identifier);
  // Auto-detect profiles a concrete target. A flat-file *batch* target (a `pattern`
  // resolved to a file only at run time) has no fixed path to profile, so suggest
  // would 422 — gate the button instead of letting it fail.
  const canSuggest = !!suite.target && !suite.target.pattern;

  return (
    <Flex vertical gap={12}>
      <Flex vertical gap={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Identifier column (shown to locate a failing row — must not be PII)
        </Typography.Text>
        <Select
          mode="tags"
          maxCount={1}
          allowClear
          loading={cols.status === 'loading'}
          disabled={!canManage}
          placeholder="e.g. order_number"
          value={identifier ? [identifier] : []}
          options={options}
          onDropdownVisibleChange={(open) => open && loadColumns()}
          onChange={(v) => setIdentifier(v[0] ?? null)}
        />
      </Flex>
      <Flex vertical gap={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          PII columns (always masked)
        </Typography.Text>
        <Select
          mode="tags"
          allowClear
          loading={cols.status === 'loading'}
          disabled={!canManage}
          placeholder="e.g. email, phone"
          value={pii}
          options={options}
          onDropdownVisibleChange={(open) => open && loadColumns()}
          onChange={setPii}
        />
      </Flex>
      {identifierIsPii && (
        <Alert
          type="warning"
          showIcon
          title="The identifier is also listed as PII — it can't be both."
        />
      )}
      {canManage && (
        <Flex gap={8}>
          <Button loading={suggesting} onClick={onSuggest} disabled={!canSuggest}>
            Auto-detect
          </Button>
          <Button type="primary" loading={saving} onClick={onSave} disabled={identifierIsPii}>
            Save
          </Button>
        </Flex>
      )}
    </Flex>
  );
}
