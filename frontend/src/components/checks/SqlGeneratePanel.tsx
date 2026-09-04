import { Alert, Button, Checkbox, Flex, type FormInstance, Input, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';

import { generateSql, runLlmFeature, type SqlGenerationResponse } from '../../api/llm';
import { errorMessage, fetchFailure } from '../../utils/errors';
import { AiCaveat } from '../shared/AiCaveat';
import { CUSTOM_SQL_QUERY_KEY } from './customSql';

/** Server-side cap on the description (`llm_sqlgen.MAX_DESCRIPTION_CHARS`); refused, never clipped. */
const MAX_DESCRIPTION = 2000;

type GenState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'done'; result: SqlGenerationResponse }
  | { status: 'failed'; error: string }
  | { status: 'unavailable'; reason: string };

/**
 * Natural-language → custom SQL (#1845, ADR 0042). Generation never creates a check: the result
 * lands in the editor below, where the same validator, dry-run and save apply unchanged.
 */
export function SqlGeneratePanel({
  suiteId,
  form,
  disabled = false,
}: {
  suiteId: string;
  form: FormInstance;
  disabled?: boolean;
}) {
  const [description, setDescription] = useState('');
  const [includeProfile, setIncludeProfile] = useState(false);
  const [state, setState] = useState<GenState>({ status: 'idle' });
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const onGenerate = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'running' });
    try {
      const row = await runLlmFeature<SqlGenerationResponse>(
        () =>
          generateSql({
            suite_id: suiteId,
            description: description.trim(),
            include_profile: includeProfile,
          }),
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      if (row.status === 'succeeded' && row.response) {
        form.setFieldValue(['config', CUSTOM_SQL_QUERY_KEY], row.response.sql);
        setState({ status: 'done', result: row.response });
      } else {
        setState({
          status: 'failed',
          error: row.error ?? 'the generation failed without a reason',
        });
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      const failure = fetchFailure(err);
      if (failure.status === 409) {
        setState({ status: 'unavailable', reason: failure.message });
      } else {
        setState({ status: 'failed', error: errorMessage(err) });
      }
    }
  };

  const tooLong = description.length > MAX_DESCRIPTION;
  const canGenerate = !disabled && description.trim().length > 0 && !tooLong;

  return (
    <Flex vertical gap={8} data-testid="sql-generate-panel">
      <Typography.Text strong>Generate from a description</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Describe the rule in plain language. The model sees the target&apos;s column names (plus
        null and distinct counts if you include the profile), never rows. Its SQL passes the same
        validator as hand-written SQL, lands in the editor below, and is not saved until you dry-run
        and create the check.
      </Typography.Text>
      <Input.TextArea
        aria-label="Rule description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="e.g. No order may have a negative quantity or a unit price of zero"
        autoSize={{ minRows: 2, maxRows: 5 }}
        disabled={disabled || state.status === 'running'}
        status={tooLong ? 'error' : undefined}
      />
      {tooLong && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          Keep the description under {MAX_DESCRIPTION} characters — it is refused, not clipped.
        </Typography.Text>
      )}
      <Flex align="center" gap={12} wrap>
        <Button
          type="primary"
          ghost
          onClick={onGenerate}
          loading={state.status === 'running'}
          disabled={!canGenerate}
        >
          Generate SQL
        </Button>
        <Checkbox
          checked={includeProfile}
          onChange={(e) => setIncludeProfile(e.target.checked)}
          disabled={disabled || state.status === 'running'}
        >
          Include column profile
        </Checkbox>
        {state.status === 'running' && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Generating on the worker — a local model can take a minute…
          </Typography.Text>
        )}
      </Flex>
      {state.status === 'done' && (
        <>
          <Alert
            type="success"
            showIcon
            title="SQL generated — review it in the editor, then dry-run"
            description={state.result.explanation || undefined}
            data-testid="sql-generate-result"
          />
          <AiCaveat />
        </>
      )}
      {state.status === 'failed' && (
        <Alert type="error" showIcon title="Generation failed" description={state.error} />
      )}
      {state.status === 'unavailable' && (
        <Alert
          type="info"
          showIcon
          title="AI generation is not enabled on this workspace"
          description={state.reason}
        />
      )}
    </Flex>
  );
}
