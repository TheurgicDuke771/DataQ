import {
  App,
  Alert,
  Badge,
  Button,
  Card,
  Flex,
  Input,
  Select,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useState } from 'react';

import { connectionOptionLabel, listConnections } from '../../api/connections';

import {
  getLlmConfig,
  testLlmConfig,
  updateLlmConfig,
  type LlmConfig,
  type LlmConfigUpdate,
  type LlmProvider,
  type LlmTestResult,
  type StructuredOutputMode,
} from '../../api/llm';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';
import { apiFieldError } from '../../utils/fieldErrors';
import { formatTimestamp } from '../results/resultsFormat';

const PROVIDER_OPTIONS: { value: LlmProvider; label: string }[] = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai_compatible', label: 'OpenAI-compatible endpoint' },
  { value: 'snowflake_cortex', label: 'Snowflake Cortex (via a connection)' },
];

const listSnowflakeConnections = () => listConnections({ type: 'snowflake' });

const STRUCTURED_OUTPUT_OPTIONS: { value: StructuredOutputMode; label: string }[] = [
  { value: 'native', label: 'Native structured output' },
  { value: 'prompt_json', label: 'Prompt-JSON fallback' },
];

/** Human labels for the /test error families — distinct from each other and from
 *  "not configured", so an outage never reads as a config gap. */
const ERROR_CODE_LABELS: Record<string, string> = {
  llm_provider_unavailable: 'Provider unavailable',
  llm_provider_error: 'Provider error',
  llm_config_invalid: 'Invalid configuration',
  llm_credential_missing: 'Credential missing',
  secret_store_unavailable: 'Secret store unavailable',
};

/** Admin → LLM: current outbound-LLM provider state + its edit form (#1511, ADR 0042). */
export function LlmSettingsPanel() {
  const { state, reload } = useAsyncData(getLlmConfig);
  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>LLM provider</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Outbound calls DataQ makes to a language model (e.g. the SQL generator).
          </Typography.Text>
        </Flex>
      }
    >
      {state.status === 'loading' && <Spin description="Loading LLM settings…" />}
      {state.status === 'error' && (
        <Alert
          type="error"
          showIcon
          title="Failed to load LLM settings"
          description={state.error}
        />
      )}
      {state.status === 'ok' && (
        <LlmForm
          // Remount on a saved config change so the form re-seeds from the loaded
          // values (render-phase reset, no setState-in-effect).
          key={`${state.data.provider}:${state.data.model}:${state.data.base_url}:${state.data.structured_output}:${state.data.enabled}:${state.data.has_credential}:${state.data.connection_id}`}
          config={state.data}
          onChanged={reload}
        />
      )}
    </Card>
  );
}

type TestState = 'idle' | 'testing' | 'ok' | 'failed';

function LlmForm({ config, onChanged }: { config: LlmConfig; onChanged: () => void }) {
  const { message } = App.useApp();
  const [provider, setProvider] = useState<LlmProvider>(config.provider ?? 'anthropic');
  const [model, setModel] = useState(config.model ?? '');
  const [baseUrl, setBaseUrl] = useState(config.base_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [structuredOutput, setStructuredOutput] = useState<StructuredOutputMode>(
    config.structured_output ?? 'native',
  );
  const [enabled, setEnabled] = useState(config.enabled);
  const [connectionId, setConnectionId] = useState<string | undefined>(
    config.connection_id ?? undefined,
  );
  const [apiKeyError, setApiKeyError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [testState, setTestState] = useState<TestState>('idle');
  const [testResult, setTestResult] = useState<LlmTestResult>();

  const baseUrlRequired = provider === 'openai_compatible';
  const cortex = provider === 'snowflake_cortex';

  const buildPayload = (): LlmConfigUpdate =>
    cortex
      ? {
          provider,
          model: model.trim(),
          connection_id: connectionId,
          structured_output: structuredOutput,
          enabled,
        }
      : {
          provider,
          model: model.trim(),
          base_url: baseUrl.trim() || undefined,
          api_key: apiKey || undefined,
          structured_output: structuredOutput,
          enabled,
        };

  // Both Save and Test can hit the same 422 — a provider/endpoint change needs the
  // key re-supplied — so the field-level handling is shared.
  const handleConfigError = (err: unknown): boolean => {
    const api = apiFieldError(err);
    if (api?.code === 'llm_config_invalid') {
      setApiKeyError(api.message);
      return true;
    }
    return false;
  };

  const onSave = async () => {
    if (cortex && !connectionId) {
      setApiKeyError(undefined);
      message.error('Choose the Snowflake connection Cortex runs under');
      return;
    }
    if (baseUrlRequired && !baseUrl.trim()) {
      setApiKeyError(undefined);
      message.error('Base URL is required for an OpenAI-compatible endpoint');
      return;
    }
    setApiKeyError(undefined);
    setSaving(true);
    try {
      await updateLlmConfig(buildPayload());
      message.success('LLM provider settings saved');
      setApiKey('');
      onChanged();
    } catch (err) {
      if (!handleConfigError(err)) message.error(`Save failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const onTest = async () => {
    setApiKeyError(undefined);
    setTestState('testing');
    setTestResult(undefined);
    try {
      const result = await testLlmConfig(buildPayload());
      setTestResult(result);
      setTestState(result.ok ? 'ok' : 'failed');
    } catch (err) {
      if (handleConfigError(err)) {
        setTestState('idle');
        return;
      }
      setTestState('failed');
      setTestResult({ ok: false, error: errorMessage(err) });
    }
  };

  return (
    <Flex vertical gap={16}>
      <Flex wrap gap={12} align="center">
        <Tag color={config.configured ? 'success' : 'default'}>
          {config.configured ? 'Configured' : 'Not configured'}
        </Tag>
        <Tag color={config.enabled ? 'success' : 'default'}>
          {config.enabled ? 'Enabled' : 'Disabled'}
        </Tag>
        <Tooltip
          title={
            config.has_credential
              ? cortex
                ? "Runs on the connection's own credential — press Test to check it still works."
                : 'A key is stored, but this does not confirm it still resolves — press Test to check.'
              : undefined
          }
        >
          <Tag color={config.has_credential ? 'success' : 'default'}>
            {config.has_credential ? 'Credential set' : 'No credential'}
          </Tag>
        </Tooltip>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Updated {formatTimestamp(config.updated_at)}
        </Typography.Text>
      </Flex>

      <Flex vertical gap={4}>
        <Typography.Text type="secondary">Provider</Typography.Text>
        <Select<LlmProvider>
          value={provider}
          onChange={setProvider}
          options={PROVIDER_OPTIONS}
          style={{ maxWidth: 320 }}
          aria-label="Provider"
        />
      </Flex>

      <Flex vertical gap={4}>
        <Typography.Text type="secondary">Model</Typography.Text>
        <Input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={cortex ? 'e.g. llama3.1-70b' : 'e.g. claude-sonnet-4-5-20250929'}
          aria-label="Model"
          style={{ maxWidth: 480 }}
        />
      </Flex>

      {cortex ? (
        <CortexConnectionField value={connectionId} onChange={setConnectionId} />
      ) : (
        <>
          <Flex vertical gap={4}>
            <Typography.Text type="secondary">
              {baseUrlRequired ? 'Base URL' : 'Base URL (advanced override)'}
            </Typography.Text>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={
                baseUrlRequired ? 'https://…/v1' : 'Leave blank for the default Anthropic endpoint'
              }
              aria-label="Base URL"
              style={{ maxWidth: 480 }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Azure OpenAI: use its <code>/openai/v1</code> base path. Ollama: use{' '}
              <code>http://host:11434/v1</code>.
            </Typography.Text>
          </Flex>

          <Flex vertical gap={4}>
            <Typography.Text type="secondary">API key</Typography.Text>
            <Input.Password
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              placeholder={config.has_credential ? 'Stored — leave blank to keep' : 'API key'}
              aria-label="API key"
              status={apiKeyError ? 'error' : undefined}
              style={{ maxWidth: 480 }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Changing provider or endpoint requires re-entering the key — the stored one is never
              forwarded to a new destination.
            </Typography.Text>
            {apiKeyError && (
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {apiKeyError}
              </Typography.Text>
            )}
          </Flex>
        </>
      )}
      {cortex && apiKeyError && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          {apiKeyError}
        </Typography.Text>
      )}

      <Flex vertical gap={4}>
        <Typography.Text type="secondary">Structured output</Typography.Text>
        <Select<StructuredOutputMode>
          value={structuredOutput}
          onChange={setStructuredOutput}
          options={STRUCTURED_OUTPUT_OPTIONS}
          style={{ maxWidth: 320 }}
          aria-label="Structured output"
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Native uses the provider's own structured-output / tool-calling support. Prompt-JSON asks
          the model to emit JSON in the prompt instead — the fallback for small local models with no
          native structured-output support.
        </Typography.Text>
      </Flex>

      <Flex align="center" gap={12}>
        <Switch checked={enabled} onChange={setEnabled} aria-label="Enable LLM" />
        <Typography.Text>Enable outbound LLM calls</Typography.Text>
      </Flex>

      <Flex align="center" gap={8} wrap>
        <Button type="primary" loading={saving} onClick={onSave}>
          Save
        </Button>
        <Button loading={testState === 'testing'} onClick={onTest}>
          Test
        </Button>
        {testState === 'ok' && testResult && (
          <Badge
            status="success"
            text={
              `OK — ${testResult.model ?? model}` +
              (testResult.latency_ms !== undefined ? ` · ${testResult.latency_ms}ms` : '') +
              (testResult.reply_chars !== undefined ? ` · ${testResult.reply_chars} chars` : '')
            }
          />
        )}
        {testState === 'failed' && testResult && (
          <Badge
            status="error"
            text={
              (testResult.error_code
                ? (ERROR_CODE_LABELS[testResult.error_code] ?? testResult.error_code)
                : 'Test failed') + (testResult.error ? `: ${testResult.error}` : '')
            }
          />
        )}
      </Flex>
    </Flex>
  );
}

/** The Snowflake connection a Cortex provider runs `CORTEX.COMPLETE` under — its credential is
 *  the provider's credential, so no API key is entered. */
function CortexConnectionField({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (id: string) => void;
}) {
  const { state } = useAsyncData(listSnowflakeConnections);
  return (
    <Flex vertical gap={4}>
      <Typography.Text type="secondary">Snowflake connection</Typography.Text>
      <Select<string>
        value={value}
        onChange={onChange}
        loading={state.status === 'loading'}
        placeholder="Choose a Snowflake connection"
        options={
          state.status === 'ok'
            ? state.data.map((c) => ({ value: c.id, label: connectionOptionLabel(c) }))
            : []
        }
        notFoundContent={state.status === 'ok' ? 'No Snowflake connections' : undefined}
        style={{ maxWidth: 480 }}
        aria-label="Snowflake connection"
      />
      {state.status === 'error' && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          Failed to load connections: {state.error}
        </Typography.Text>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        The model runs inside that Snowflake account under the connection&apos;s role, which needs
        the <code>SNOWFLAKE.CORTEX_USER</code> database role. No API key is stored.
      </Typography.Text>
    </Flex>
  );
}
